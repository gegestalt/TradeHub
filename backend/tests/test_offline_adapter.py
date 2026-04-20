"""Tests for OfflineDataAdapter — CSV loading and time-aware price lookup."""

import csv
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from data_adapters.offline import OfflineDataAdapter

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Open", "High", "Low", "Close", "Volume"])
        writer.writeheader()
        writer.writerows(rows)


def _make_rows(start: datetime, n: int, base: float = 100.0, step: float = 1.0) -> list[dict]:
    """Generate n hourly OHLCV rows with predictable close prices."""
    rows = []
    for i in range(n):
        close = round(base + i * step, 2)
        rows.append({
            "Date": (start + timedelta(hours=i)).isoformat(),
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 1_000_000,
        })
    return rows


@pytest.fixture()
def data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture()
def adapter_with_aapl(data_dir):
    """OfflineDataAdapter with 24 hourly AAPL rows starting 2024-01-01 00:00."""
    start = datetime(2024, 1, 1, 0, 0)
    rows = _make_rows(start, n=24, base=100.0, step=1.0)
    _write_csv(data_dir / "AAPL.csv", rows)
    return data_dir, start, rows


# ── Basic CSV loading ─────────────────────────────────────────────────────────


def test_loads_csv_and_returns_last_price(adapter_with_aapl):
    data_dir, _, rows = adapter_with_aapl
    adapter = OfflineDataAdapter(str(data_dir))
    # No competition context → latest row
    assert adapter.get_price("AAPL") == Decimal(str(rows[-1]["Close"]))


def test_raises_for_missing_ticker(data_dir):
    adapter = OfflineDataAdapter(str(data_dir))
    with pytest.raises(ValueError, match="No offline data found"):
        adapter.get_price("MISSING")


def test_list_tickers_returns_csv_stems(adapter_with_aapl):
    data_dir, _, _ = adapter_with_aapl
    adapter = OfflineDataAdapter(str(data_dir))
    assert "AAPL" in adapter.list_tickers()


def test_csv_rows_sorted_by_timestamp(data_dir):
    start = datetime(2024, 1, 1, 0, 0)
    # Write in reverse order to verify sorting
    rows = list(reversed(_make_rows(start, n=5)))
    _write_csv(data_dir / "AAPL.csv", rows)
    adapter = OfflineDataAdapter(str(data_dir))
    # After load, rows should be sorted; get_price(at=...) should work correctly
    price_at_start = adapter.get_price("AAPL", at=start)
    assert price_at_start == Decimal("100.0")


# ── Time-aware price lookup ───────────────────────────────────────────────────


def test_at_competition_start_returns_first_row(adapter_with_aapl):
    data_dir, data_start, rows = adapter_with_aapl
    comp_start = datetime.utcnow()
    adapter = OfflineDataAdapter(str(data_dir), competition_start_at=comp_start)

    # elapsed ≈ 0 → effective_time ≈ data_start → first row's price
    price = adapter.get_price("AAPL")
    assert price == Decimal(str(rows[0]["Close"]))


def test_one_hour_elapsed_returns_second_row(adapter_with_aapl):
    data_dir, data_start, rows = adapter_with_aapl
    # Set competition_start_at 1 hour in the past so elapsed = 1h
    comp_start = datetime.utcnow() - timedelta(hours=1)
    adapter = OfflineDataAdapter(str(data_dir), competition_start_at=comp_start)

    price = adapter.get_price("AAPL")
    # At 1h elapsed we should be on the second row
    assert price == Decimal(str(rows[1]["Close"]))


def test_elapsed_beyond_data_range_returns_last_row(adapter_with_aapl):
    data_dir, data_start, rows = adapter_with_aapl
    # 30 days elapsed but data only covers 24 hours → clamp to last row
    comp_start = datetime.utcnow() - timedelta(days=30)
    adapter = OfflineDataAdapter(str(data_dir), competition_start_at=comp_start)

    price = adapter.get_price("AAPL")
    assert price == Decimal(str(rows[-1]["Close"]))


def test_explicit_at_overrides_competition_time(adapter_with_aapl):
    data_dir, data_start, rows = adapter_with_aapl
    comp_start = datetime.utcnow() - timedelta(hours=10)
    adapter = OfflineDataAdapter(str(data_dir), competition_start_at=comp_start)

    # Explicit at= 3rd row timestamp → should return 3rd row price regardless of elapsed
    target_ts = data_start + timedelta(hours=2)
    price = adapter.get_price("AAPL", at=target_ts)
    assert price == Decimal(str(rows[2]["Close"]))


def test_no_competition_context_returns_latest(adapter_with_aapl):
    data_dir, _, rows = adapter_with_aapl
    adapter = OfflineDataAdapter(str(data_dir))  # no competition_start_at
    assert adapter.get_price("AAPL") == Decimal(str(rows[-1]["Close"]))


# ── data_range helper ─────────────────────────────────────────────────────────


def test_data_range_returns_first_and_last_timestamps(adapter_with_aapl):
    data_dir, data_start, rows = adapter_with_aapl
    adapter = OfflineDataAdapter(str(data_dir))
    first, last = adapter.data_range("AAPL")
    assert first == data_start
    assert last == data_start + timedelta(hours=23)


def test_data_range_returns_none_for_missing_ticker(data_dir):
    adapter = OfflineDataAdapter(str(data_dir))
    assert adapter.data_range("MISSING") is None


# ── get_ohlcv ─────────────────────────────────────────────────────────────────


def test_get_ohlcv_filters_by_range(adapter_with_aapl):
    data_dir, data_start, rows = adapter_with_aapl
    adapter = OfflineDataAdapter(str(data_dir))
    start = data_start + timedelta(hours=5)
    end = data_start + timedelta(hours=10)
    candles = adapter.get_ohlcv("AAPL", start, end)
    assert all(start <= c.timestamp <= end for c in candles)
    assert len(candles) == 6  # hours 5–10 inclusive


def test_get_ohlcv_empty_for_out_of_range(adapter_with_aapl):
    data_dir, data_start, _ = adapter_with_aapl
    adapter = OfflineDataAdapter(str(data_dir))
    future = data_start + timedelta(days=365)
    assert adapter.get_ohlcv("AAPL", future, future + timedelta(hours=1)) == []


# ── Bundled data smoke test ───────────────────────────────────────────────────


def test_bundled_csv_files_load(tmp_path):
    """Verify the bundled data files in backend/data/ are loadable."""
    bundled = Path(__file__).parent.parent / "data"
    if not bundled.exists():
        pytest.skip("bundled data directory not found")

    adapter = OfflineDataAdapter(str(bundled))
    tickers = adapter.list_tickers()
    assert len(tickers) >= 1

    for ticker in tickers:
        price = adapter.get_price(ticker)
        assert price > Decimal("0"), f"{ticker} returned non-positive price"
