"""Live data adapter tests — calls yfinance directly. Always hits the network.

Run all:
    pytest tests/test_online_adapter.py -v -s

Run a single ticker spot check:
    pytest tests/test_online_adapter.py::test_get_price_aapl -v -s

These are kept separate from the main suite so CI can skip them with:
    pytest tests/ --ignore=tests/test_online_adapter.py
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from data_adapters.online import OnlineDataAdapter

LIQUID_TICKERS = ["AAPL", "TSLA", "GOOGL", "MSFT", "BTC-USD"]


@pytest.fixture(scope="module")
def adapter():
    return OnlineDataAdapter()


# ── Price fetching ────────────────────────────────────────────────────────────


def test_get_price_aapl(adapter):
    price = adapter.get_price("AAPL")
    assert isinstance(price, Decimal)
    assert price > Decimal("1"), f"AAPL price suspiciously low: {price}"
    print(f"\n  AAPL last price: ${price}")


def test_get_price_tsla(adapter):
    price = adapter.get_price("TSLA")
    assert price > Decimal("1"), f"TSLA price suspiciously low: {price}"
    print(f"\n  TSLA last price: ${price}")


def test_get_price_btc(adapter):
    price = adapter.get_price("BTC-USD")
    assert price > Decimal("100"), f"BTC-USD price suspiciously low: {price}"
    print(f"\n  BTC-USD last price: ${price}")


def test_all_liquid_tickers_return_positive_price(adapter):
    print()
    for ticker in LIQUID_TICKERS:
        price = adapter.get_price(ticker)
        assert price > Decimal("0"), f"{ticker} returned non-positive price: {price}"
        print(f"  {ticker:<10} ${price:,.2f}")


def test_price_is_decimal(adapter):
    price = adapter.get_price("MSFT")
    assert isinstance(price, Decimal)


def test_unknown_ticker_raises(adapter):
    with pytest.raises((ValueError, Exception)):
        adapter.get_price("TOTALLY_FAKE_TICKER_XYZ999")


# ── OHLCV history ─────────────────────────────────────────────────────────────


def test_get_ohlcv_returns_rows(adapter):
    end = datetime.utcnow()
    start = end - timedelta(days=30)
    rows = adapter.get_ohlcv("AAPL", start, end)
    assert len(rows) > 0, "Expected at least 1 OHLCV row for AAPL over 30 days"
    print(f"\n  AAPL: {len(rows)} daily candles over last 30 days")


def test_get_ohlcv_fields_populated(adapter):
    end = datetime.utcnow()
    start = end - timedelta(days=7)
    rows = adapter.get_ohlcv("AAPL", start, end)
    assert rows, "No OHLCV data returned"
    row = rows[-1]
    assert row.ticker == "AAPL"
    assert row.open > Decimal("0")
    assert row.high >= row.low
    assert row.close > Decimal("0")
    assert row.volume >= Decimal("0")
    assert isinstance(row.timestamp, datetime)
    print(f"\n  Latest candle: O={row.open} H={row.high} L={row.low} C={row.close} V={row.volume}")


def test_get_ohlcv_high_always_gte_low(adapter):
    end = datetime.utcnow()
    start = end - timedelta(days=30)
    rows = adapter.get_ohlcv("TSLA", start, end)
    for row in rows:
        assert row.high >= row.low, (
            f"TSLA: high {row.high} < low {row.low} on {row.timestamp.date()}"
        )


def test_get_ohlcv_chronologically_ordered(adapter):
    end = datetime.utcnow()
    start = end - timedelta(days=30)
    rows = adapter.get_ohlcv("GOOGL", start, end)
    if len(rows) > 1:
        for i in range(1, len(rows)):
            assert rows[i].timestamp >= rows[i - 1].timestamp, "Rows not in chronological order"


def test_get_ohlcv_empty_for_future_dates(adapter):
    start = datetime.utcnow() + timedelta(days=365)
    end = start + timedelta(days=7)
    rows = adapter.get_ohlcv("AAPL", start, end)
    assert rows == [], f"Expected empty list for future dates, got {len(rows)} rows"


# ── Price consistency ─────────────────────────────────────────────────────────


def test_two_calls_return_same_ticker_magnitude(adapter):
    """Calling get_price twice should return prices in the same order of magnitude."""
    p1 = adapter.get_price("AAPL")
    p2 = adapter.get_price("AAPL")
    ratio = float(max(p1, p2)) / float(min(p1, p2))
    assert ratio < 1.05, (
        f"Two AAPL price calls differed by more than 5%: {p1} vs {p2}"
    )


def test_get_price_with_historical_at(adapter):
    """get_price(at=...) should return a historical close near that date."""
    historical_date = datetime(2024, 1, 2)  # known trading day
    price = adapter.get_price("AAPL", at=historical_date)
    assert price > Decimal("100"), f"Historical AAPL price unusually low: {price}"
    # AAPL was roughly $180-$195 in Jan 2024
    assert price < Decimal("500"), f"Historical AAPL price unusually high: {price}"
    print(f"\n  AAPL on 2024-01-02: ${price}")


# ── Multi-ticker summary (manual spot check) ──────────────────────────────────


def test_print_market_snapshot(adapter):
    """Prints a human-readable market snapshot — useful for manual runs with -s."""
    print("\n" + "═" * 50)
    print(f"  Market Snapshot — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("─" * 50)
    for ticker in LIQUID_TICKERS:
        try:
            price = adapter.get_price(ticker)
            end = datetime.utcnow()
            rows = adapter.get_ohlcv(ticker, end - timedelta(days=2), end)
            if len(rows) >= 2:
                prev_close = rows[-2].close
                change = price - prev_close
                pct = float(change / prev_close * 100)
                sign = "+" if pct >= 0 else ""
                print(f"  {ticker:<10} ${float(price):>10,.2f}   {sign}{pct:.2f}%")
            else:
                print(f"  {ticker:<10} ${float(price):>10,.2f}")
        except Exception as exc:
            print(f"  {ticker:<10} ERROR: {exc}")
    print("═" * 50)
