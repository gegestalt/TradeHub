"""Tests for the player analysis service and endpoint."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide, OrderType
from schemas.competition import CompetitionCreate
from schemas.order import OCOCreate, OrderCreate
from services.analysis import generate_player_analysis
from services.competition import create_competition, start_competition
from services.order_engine import place_oco_order, place_order


def make_adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(db, balance: Decimal = Decimal("50000"), allow_shorts: bool = False):
    from tests.conftest import make_user
    user, _ = await make_user(db, "Analyst")
    data = CompetitionCreate(
        name="Analysis Test",
        starting_balance=balance,
        asset_universe=["AAPL", "BTC-USD", "TSLA", "MSFT"],
        fee_pct=Decimal("0.001"),
        allow_shorts=allow_shorts,
    )
    comp, player, _ = await create_competition(db, data, user)
    await start_competition(db, comp.lobby_code, player)
    return comp, player


# ── Inactive player ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_inactive_player(db):
    comp, player = await _setup(db)
    report = await generate_player_analysis(db, player, comp)

    assert report.trading_style == "inactive"
    assert report.orders_filled == 0
    assert report.total_orders_placed == 0
    assert "did not execute" in report.commentary
    assert report.round_trips_completed == 0
    assert report.techniques_identified == []


# ── Buy-and-hold ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_buy_and_hold(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("150")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.trading_style == "buy_and_hold"
    assert report.orders_filled == 1
    assert report.round_trips_completed == 0
    assert "AAPL" in report.tickers_traded
    assert "Buy-and-hold (never sold)" in report.techniques_identified
    assert report.most_traded_ticker == "AAPL"


# ── Round trip: buy then sell ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_completed_round_trip(db):
    comp, player = await _setup(db, balance=Decimal("100000"))
    adapter100 = make_adapter(Decimal("100"))
    adapter130 = make_adapter(Decimal("130"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        adapter100,
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        adapter130,
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.round_trips_completed == 1
    assert report.win_rate_pct == 100.0
    assert report.best_trade_ticker == "AAPL"
    assert report.best_trade_pnl is not None and report.best_trade_pnl > 0
    assert report.total_realized_pnl > 0
    # Profit locking detected
    assert "Profit locking (closed positions)" in report.techniques_identified


@pytest.mark.asyncio
async def test_analysis_losing_round_trip(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("80")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.round_trips_completed == 1
    assert report.win_rate_pct == 0.0
    assert report.worst_trade_pnl is not None and report.worst_trade_pnl < 0


# ── Win rate with mixed results ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_mixed_win_rate(db):
    comp, player = await _setup(db, balance=Decimal("200000"))

    # Win on AAPL
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("120")),
    )

    # Loss on BTC-USD
    await place_order(
        db, player, comp,
        OrderCreate(ticker="BTC-USD", side=OrderSide.buy, quantity=Decimal("1")),
        make_adapter(Decimal("50000")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="BTC-USD", side=OrderSide.sell, quantity=Decimal("1")),
        make_adapter(Decimal("40000")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.round_trips_completed == 2
    assert report.win_rate_pct == 50.0
    assert set(report.tickers_traded) == {"AAPL", "BTC-USD"}
    assert report.diversification_score > 0


# ── Diversification detection ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_diversified_portfolio(db):
    comp, player = await _setup(db, balance=Decimal("500000"))

    for ticker, price in [("AAPL", "100"), ("BTC-USD", "30000"), ("TSLA", "200"), ("MSFT", "300")]:
        await place_order(
            db, player, comp,
            OrderCreate(ticker=ticker, side=OrderSide.buy, quantity=Decimal("1")),
            make_adapter(Decimal(price)),
        )

    report = await generate_player_analysis(db, player, comp)

    assert len(report.tickers_traded) == 4
    assert report.diversification_score == 1.0  # 4/4 universe assets
    assert "Portfolio diversification" in report.techniques_identified


@pytest.mark.asyncio
async def test_analysis_single_asset_concentration(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.diversification_score == 0.25  # 1/4 assets
    assert "Single-asset concentration" in report.techniques_identified


# ── Risk management detection ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_detects_stop_loss_usage(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.sell,
            order_type=OrderType.stop_loss,
            quantity=Decimal("5"), stop_price=Decimal("50"),
        ),
        make_adapter(Decimal("100")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.used_stop_loss is True
    assert "Stop-loss risk management" in report.techniques_identified


@pytest.mark.asyncio
async def test_analysis_detects_oco_usage(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )
    await place_oco_order(
        db, player, comp,
        OCOCreate(
            ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5"),
            stop_price=Decimal("50"), take_profit_price=Decimal("999"),
        ),
        make_adapter(Decimal("100")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.used_oco is True
    assert "OCO (One-Cancels-Other) bracket orders" in report.techniques_identified


@pytest.mark.asyncio
async def test_analysis_detects_limit_order_usage(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy,
            order_type=OrderType.limit,
            quantity=Decimal("5"), limit_price=Decimal("200"),
        ),
        make_adapter(Decimal("100")),  # fills immediately at 100 (≤ limit of 200)
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.used_limit_orders is True
    assert "Limit order execution (price discipline)" in report.techniques_identified


# ── Averaging down detection ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_detects_averaging_down(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    # Buy at 100, then again at 80 (dip buying / averaging down)
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("80")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert any("averaging" in t.lower() or "dip" in t.lower() for t in report.techniques_identified)


@pytest.mark.asyncio
async def test_analysis_detects_pyramiding(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    # Buy at 100, then again at 120 (pyramiding into winner)
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("3")),
        make_adapter(Decimal("120")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert any("pyramid" in t.lower() for t in report.techniques_identified)


# ── Narrative fields ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_strengths_with_stop_loss(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.sell,
            order_type=OrderType.stop_loss,
            quantity=Decimal("5"), stop_price=Decimal("50"),
        ),
        make_adapter(Decimal("100")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert any("stop-loss" in s.lower() for s in report.strengths)


@pytest.mark.asyncio
async def test_analysis_improvement_suggested_for_no_stop_loss(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert any("stop-loss" in imp.lower() for imp in report.improvements)


@pytest.mark.asyncio
async def test_analysis_commentary_is_non_empty(db):
    comp, player = await _setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5")),
        make_adapter(Decimal("120")),
    )

    report = await generate_player_analysis(db, player, comp)

    assert len(report.commentary) > 20
    assert "Analyst" in report.commentary


# ── Fee tracking ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_tracks_fees(db):
    comp, player = await _setup(db, balance=Decimal("100000"))
    price = Decimal("100")
    qty = Decimal("10")
    fee_pct = comp.fee_pct

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=qty),
        make_adapter(price),
    )

    report = await generate_player_analysis(db, player, comp)

    expected_fee = price * qty * fee_pct
    assert abs(report.total_fees_paid - expected_fee) < Decimal("0.01")


# ── Cancelled and expired orders not counted as fills ─────────────────────────


@pytest.mark.asyncio
async def test_analysis_cancelled_orders_tracked_separately(db):
    from models.enums import TimeInForce
    comp, player = await _setup(db, balance=Decimal("100000"))

    # IOC order that won't fill (price above limit)
    await place_order(
        db, player, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy,
            order_type=OrderType.limit,
            quantity=Decimal("5"), limit_price=Decimal("50"),
            time_in_force=TimeInForce.ioc,
        ),
        make_adapter(Decimal("200")),  # price above limit → expired
    )

    report = await generate_player_analysis(db, player, comp)

    assert report.orders_filled == 0
    assert report.orders_expired == 1
    assert report.trading_style == "inactive"


# ── HTTP endpoint ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_endpoint_returns_200(client):
    from tests.conftest import register_http

    reg = await register_http(client, "EndpointPlayer")
    create_resp = await client.post(
        "/competitions",
        json={
            "name": "Analysis Endpoint Test",
            "asset_universe": ["AAPL", "BTC-USD"],
            "starting_balance": "100000",
            "data_source": "mock",
        },
        headers={"Authorization": f"Bearer {reg['token']}"},
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    code = body["lobby_code"]
    player_token = body["token"]
    player_id = body["player_id"]

    await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {player_token}"},
    )

    resp = await client.get(
        f"/competitions/{code}/players/{player_id}/analysis",
        headers={"Authorization": f"Bearer {player_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    for field in (
        "trading_style", "risk_profile", "techniques_identified",
        "commentary", "strengths", "improvements", "round_trips",
        "total_realized_pnl", "orders_filled",
    ):
        assert field in data, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_analysis_endpoint_forbidden_for_other_player(client):
    from tests.conftest import register_http

    alice = await register_http(client, "AliceA")
    bob = await register_http(client, "BobA")

    create_resp = await client.post(
        "/competitions",
        json={
            "name": "Privacy Test",
            "asset_universe": ["AAPL"],
            "starting_balance": "10000",
            "data_source": "mock",
        },
        headers={"Authorization": f"Bearer {alice['token']}"},
    )
    body = create_resp.json()
    code = body["lobby_code"]
    alice_player_id = body["player_id"]
    alice_player_token = body["token"]

    bob_join = await client.post(
        f"/competitions/{code}/join",
        json={"spectator": False},
        headers={"Authorization": f"Bearer {bob['token']}"},
    )
    bob_player_token = bob_join.json()["token"]

    # Bob cannot view Alice's analysis
    resp = await client.get(
        f"/competitions/{code}/players/{alice_player_id}/analysis",
        headers={"Authorization": f"Bearer {bob_player_token}"},
    )
    assert resp.status_code == 403
