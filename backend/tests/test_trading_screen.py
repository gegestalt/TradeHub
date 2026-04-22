"""Trading screen feature tests — written before implementation (TDD).

Covers:
  1. Competition-scoped candles  GET /competitions/{code}/prices/{ticker}/candles
  2. Competition benchmark        GET /competitions/{code}/benchmark
  3. Trade marks / annotations    GET /competitions/{code}/players/{id}/trade-marks/{ticker}
  4. WebSocket price_tick message format (unit tests, no network)
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from data_adapters.base import OHLCV
from data_adapters.mock import _registry
from models.enums import DataSource
from services.snapshot_task import _snapshot_competition
from services.websocket_manager import price_tick_message
from tests.conftest import create_lobby_http, join_http, register_http

# ── Fixtures & helpers ─────────────────────────────────────────────────────────

def _comp_payload(**overrides) -> dict:
    base = {
        "name": "Trading Screen Test",
        "starting_balance": "10000",
        "asset_universe": ["AAPL", "TSLA"],
        "data_source": "mock",
    }
    base.update(overrides)
    return base


def _make_stub_ohlcv(ticker: str, n: int = 5) -> list[OHLCV]:
    """Return n synthetic daily candles for use in stub adapter."""
    base = datetime(2024, 1, 2)
    rows = []
    for i in range(n):
        price = Decimal(str(100 + i * 2))
        rows.append(OHLCV(
            ticker=ticker,
            open=price,
            high=price + Decimal("1"),
            low=price - Decimal("1"),
            close=price + Decimal("0.5"),
            volume=Decimal("10000"),
            timestamp=base + timedelta(days=i),
        ))
    return rows


class _StubOHLCVAdapter:
    """Minimal adapter that returns canned OHLCV data — no external calls."""
    source = DataSource.mock

    def __init__(self, rows: list[OHLCV]) -> None:
        self._rows = rows

    def get_price(self, ticker: str, at=None) -> Decimal:
        return Decimal("150.00")

    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]:
        return [r for r in self._rows if r.ticker == ticker]

    def list_tickers(self) -> list[str]:
        return list({r.ticker for r in self._rows})


@pytest.fixture(autouse=True)
def clear_mock_registry():
    import data_adapters.mock as _m
    with _m._lock:
        _registry.clear()
    yield
    with _m._lock:
        _registry.clear()


async def _create_active_competition(client) -> dict:
    """Create, start and return {code, token, player_id}."""
    ctx = await create_lobby_http(client, "Alice", **_comp_payload())
    code = ctx["code"]
    token = ctx["player_token"]
    start = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert start.status_code == 200
    lb = await client.get(f"/competitions/{code}/leaderboard")
    player_id = lb.json()[0]["player_id"]
    return {"code": code, "token": token, "player_id": player_id}


# ── 1. Competition-scoped candles ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_competition_candles_returns_200_for_valid_ticker(client):
    ctx = await _create_active_competition(client)
    resp = await client.get(f"/competitions/{ctx['code']}/prices/AAPL/candles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert body["competition_code"] == ctx["code"]
    assert "candles" in body
    assert isinstance(body["candles"], list)


@pytest.mark.asyncio
async def test_competition_candles_includes_timeframe_in_response(client):
    ctx = await _create_active_competition(client)
    resp = await client.get(
        f"/competitions/{ctx['code']}/prices/AAPL/candles?timeframe=1d"
    )
    assert resp.status_code == 200
    assert resp.json()["timeframe"] == "1d"


@pytest.mark.asyncio
async def test_competition_candles_returns_400_for_ticker_not_in_universe(client):
    ctx = await _create_active_competition(client)
    resp = await client.get(f"/competitions/{ctx['code']}/prices/GOOGL/candles")
    assert resp.status_code == 400
    assert "GOOGL" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_competition_candles_returns_404_for_unknown_competition(client):
    resp = await client.get("/competitions/XXXXXX/prices/AAPL/candles")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_competition_candles_ticker_lookup_is_case_insensitive(client):
    ctx = await _create_active_competition(client)
    resp = await client.get(f"/competitions/{ctx['code']}/prices/aapl/candles")
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_competition_candles_candle_fields_are_correct(client, db):
    """With a stub adapter returning real OHLCV rows, fields must match spec."""
    stub = _StubOHLCVAdapter(_make_stub_ohlcv("AAPL", n=3))

    ctx = await _create_active_competition(client)

    from models.competition import Competition
    result = await db.execute(
        select(Competition).where(Competition.lobby_code == ctx["code"])
    )
    comp = result.scalar_one()

    with patch("services.candle_service.get_adapter", return_value=stub):
        resp = await client.get(f"/competitions/{ctx['code']}/prices/AAPL/candles")

    assert resp.status_code == 200
    candles = resp.json()["candles"]
    assert len(candles) == 3
    for c in candles:
        for field in ("t", "o", "h", "l", "c", "v"):
            assert field in c, f"Missing candle field '{field}'"
        assert Decimal(c["h"]) >= Decimal(c["l"]), "high must be >= low"


@pytest.mark.asyncio
async def test_competition_candles_limit_parameter_respected(client, db):
    stub = _StubOHLCVAdapter(_make_stub_ohlcv("AAPL", n=10))
    ctx = await _create_active_competition(client)

    with patch("services.candle_service.get_adapter", return_value=stub):
        resp = await client.get(
            f"/competitions/{ctx['code']}/prices/AAPL/candles?limit=3"
        )
    assert resp.status_code == 200
    assert len(resp.json()["candles"]) <= 3


@pytest.mark.asyncio
async def test_competition_candles_works_for_ended_competition(client):
    """Ended competitions should still serve historical candles for replay."""
    ctx = await create_lobby_http(client, "Alice", **_comp_payload())
    code = ctx["code"]
    token = ctx["player_token"]

    await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        f"/competitions/{code}/end",
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(f"/competitions/{code}/prices/AAPL/candles")
    assert resp.status_code == 200


# ── 2. Competition benchmark ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_benchmark_returns_empty_series_when_no_snapshots(client):
    ctx = await _create_active_competition(client)
    resp = await client.get(f"/competitions/{ctx['code']}/benchmark")
    assert resp.status_code == 200
    body = resp.json()
    assert body["competition_code"] == ctx["code"]
    assert body["series"] == []


@pytest.mark.asyncio
async def test_benchmark_returns_correct_fields(client, db):
    ctx = await _create_active_competition(client)

    from models.competition import Competition
    result = await db.execute(
        select(Competition).where(Competition.lobby_code == ctx["code"])
    )
    comp = result.scalar_one()
    await _snapshot_competition(db, comp)
    await db.flush()

    resp = await client.get(f"/competitions/{ctx['code']}/benchmark")
    assert resp.status_code == 200
    series = resp.json()["series"]
    assert len(series) >= 1

    entry = series[0]
    for field in ("timestamp", "avg_total_value", "avg_pnl", "avg_pnl_pct", "player_count"):
        assert field in entry, f"Missing benchmark field '{field}'"


@pytest.mark.asyncio
async def test_benchmark_initial_pnl_is_zero_before_any_trades(client, db):
    ctx = await _create_active_competition(client)

    from models.competition import Competition
    result = await db.execute(
        select(Competition).where(Competition.lobby_code == ctx["code"])
    )
    comp = result.scalar_one()
    await _snapshot_competition(db, comp)
    await db.flush()

    resp = await client.get(f"/competitions/{ctx['code']}/benchmark")
    entry = resp.json()["series"][0]
    assert float(entry["avg_pnl"]) == 0.0
    assert float(entry["avg_pnl_pct"]) == pytest.approx(0.0, abs=0.001)


@pytest.mark.asyncio
async def test_benchmark_multiple_snapshots_produce_multiple_entries(client, db):
    ctx = await _create_active_competition(client)

    from models.competition import Competition
    result = await db.execute(
        select(Competition).where(Competition.lobby_code == ctx["code"])
    )
    comp = result.scalar_one()
    await _snapshot_competition(db, comp)
    await _snapshot_competition(db, comp)
    await db.flush()

    resp = await client.get(f"/competitions/{ctx['code']}/benchmark")
    assert len(resp.json()["series"]) == 2


@pytest.mark.asyncio
async def test_benchmark_series_is_chronologically_ordered(client, db):
    ctx = await _create_active_competition(client)

    from models.competition import Competition
    result = await db.execute(
        select(Competition).where(Competition.lobby_code == ctx["code"])
    )
    comp = result.scalar_one()
    await _snapshot_competition(db, comp)
    await _snapshot_competition(db, comp)
    await db.flush()

    resp = await client.get(f"/competitions/{ctx['code']}/benchmark")
    series = resp.json()["series"]
    timestamps = [e["timestamp"] for e in series]
    assert timestamps == sorted(timestamps), "Benchmark series not chronologically ordered"


@pytest.mark.asyncio
async def test_benchmark_player_count_matches_active_players(client, db):
    """Benchmark player_count must equal number of non-spectator players."""
    ctx = await create_lobby_http(client, "Alice", **_comp_payload())
    code = ctx["code"]
    token = ctx["player_token"]

    # Bob joins before start
    await join_http(client, code, "Bob")
    await join_http(client, code, "Watcher", spectator=True)
    await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {token}"},
    )

    from models.competition import Competition
    result = await db.execute(select(Competition).where(Competition.lobby_code == code))
    comp = result.scalar_one()
    await _snapshot_competition(db, comp)
    await db.flush()

    resp = await client.get(f"/competitions/{code}/benchmark")
    entry = resp.json()["series"][0]
    assert entry["player_count"] == 2  # Alice + Bob, not the spectator


@pytest.mark.asyncio
async def test_benchmark_returns_404_for_unknown_competition(client):
    resp = await client.get("/competitions/XXXXXX/benchmark")
    assert resp.status_code == 404


# ── 3. Trade marks (chart annotations) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_trade_marks_empty_for_player_with_no_fills(client):
    ctx = await _create_active_competition(client)
    resp = await client.get(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/trade-marks/AAPL",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["marks"] == []
    assert body["ticker"] == "AAPL"
    assert body["player_id"] == ctx["player_id"]


@pytest.mark.asyncio
async def test_trade_marks_returns_filled_order_for_ticker(client):
    ctx = await _create_active_competition(client)
    await client.post(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/orders",
        headers={"Authorization": f"Bearer {ctx['token']}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "5"},
    )

    resp = await client.get(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/trade-marks/AAPL",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert resp.status_code == 200
    marks = resp.json()["marks"]
    assert len(marks) == 1
    assert marks[0]["side"] == "buy"
    assert Decimal(marks[0]["quantity"]) == Decimal("5")


@pytest.mark.asyncio
async def test_trade_marks_correct_fields_on_each_mark(client):
    ctx = await _create_active_competition(client)
    await client.post(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/orders",
        headers={"Authorization": f"Bearer {ctx['token']}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "3"},
    )

    resp = await client.get(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/trade-marks/AAPL",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    mark = resp.json()["marks"][0]
    for field in ("fill_at", "fill_price", "side", "quantity", "fee_paid", "order_type"):
        assert field in mark, f"Missing trade mark field '{field}'"
    assert Decimal(mark["fill_price"]) > Decimal("0")


@pytest.mark.asyncio
async def test_trade_marks_excludes_fills_for_other_tickers(client):
    ctx = await _create_active_competition(client)
    # Buy AAPL and TSLA
    await client.post(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/orders",
        headers={"Authorization": f"Bearer {ctx['token']}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "2"},
    )
    await client.post(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/orders",
        headers={"Authorization": f"Bearer {ctx['token']}"},
        json={"ticker": "TSLA", "side": "buy", "quantity": "1"},
    )

    # Trade marks for AAPL only
    resp = await client.get(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/trade-marks/AAPL",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    marks = resp.json()["marks"]
    assert len(marks) == 1
    assert all(m["side"] == "buy" for m in marks)


@pytest.mark.asyncio
async def test_trade_marks_multiple_fills_ordered_chronologically(client):
    ctx = await _create_active_competition(client)
    for _ in range(3):
        await client.post(
            f"/competitions/{ctx['code']}/players/{ctx['player_id']}/orders",
            headers={"Authorization": f"Bearer {ctx['token']}"},
            json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
        )

    resp = await client.get(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/trade-marks/AAPL",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    marks = resp.json()["marks"]
    assert len(marks) == 3
    fill_times = [m["fill_at"] for m in marks]
    assert fill_times == sorted(fill_times), "Trade marks not in chronological order"


@pytest.mark.asyncio
async def test_trade_marks_excludes_pending_limit_orders(client):
    ctx = await _create_active_competition(client)
    # Place a limit buy far below market — will stay pending
    await client.post(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/orders",
        headers={"Authorization": f"Bearer {ctx['token']}"},
        json={
            "ticker": "AAPL", "side": "buy", "quantity": "1",
            "order_type": "limit", "limit_price": "0.01", "time_in_force": "gtc",
        },
    )

    resp = await client.get(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/trade-marks/AAPL",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    # Pending order must NOT appear as a trade mark
    assert resp.json()["marks"] == []


@pytest.mark.asyncio
async def test_trade_marks_requires_authentication(client):
    ctx = await _create_active_competition(client)
    resp = await client.get(
        f"/competitions/{ctx['code']}/players/{ctx['player_id']}/trade-marks/AAPL"
    )
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_trade_marks_rejects_cross_player_access(client):
    ctx = await create_lobby_http(client, "Alice", **_comp_payload())
    code = ctx["code"]
    alice_token = ctx["player_token"]

    bob = await join_http(client, code, "Bob")
    bob_id = bob["player_id"]

    await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {alice_token}"},
    )

    # Alice tries to read Bob's marks — must be 403
    resp = await client.get(
        f"/competitions/{code}/players/{bob_id}/trade-marks/AAPL",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_trade_marks_returns_404_for_unknown_competition(client):
    ctx = await _create_active_competition(client)
    resp = await client.get(
        f"/competitions/XXXXXX/players/{ctx['player_id']}/trade-marks/AAPL",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert resp.status_code == 404


# ── 4. WebSocket price_tick message format ─────────────────────────────────────


def test_price_tick_message_has_correct_type():
    msg = price_tick_message("AAPL", Decimal("150.00"), datetime(2024, 1, 2, 10, 30))
    assert msg["type"] == "price_tick"


def test_price_tick_message_includes_ticker():
    msg = price_tick_message("TSLA", Decimal("200.00"), datetime(2024, 1, 2, 10, 30))
    assert msg["ticker"] == "TSLA"


def test_price_tick_message_price_is_string():
    msg = price_tick_message("AAPL", Decimal("150.12345"), datetime(2024, 1, 2, 10, 30))
    assert isinstance(msg["price"], str)
    assert Decimal(msg["price"]) == Decimal("150.12345")


def test_price_tick_message_timestamp_is_iso_string():
    ts = datetime(2024, 1, 2, 10, 30, 0)
    msg = price_tick_message("AAPL", Decimal("150.00"), ts)
    assert isinstance(msg["timestamp"], str)
    assert "2024-01-02" in msg["timestamp"]


def test_price_tick_message_has_all_required_fields():
    msg = price_tick_message("AAPL", Decimal("150.00"), datetime(2024, 1, 2, 10, 30))
    for field in ("type", "ticker", "price", "timestamp"):
        assert field in msg, f"price_tick_message missing field '{field}'"
