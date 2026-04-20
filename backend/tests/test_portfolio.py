"""Tests for the portfolio service (unrealized P&L, position details)."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide
from schemas.competition import CompetitionCreate
from schemas.order import OrderCreate
from services.competition import create_competition, start_competition
from services.order_engine import place_order
from services.portfolio import get_portfolio


def make_adapter(price: Decimal) -> MagicMock:
    adapter = MagicMock()
    adapter.get_price.return_value = price
    return adapter


def make_multi_adapter(prices: dict[str, Decimal]) -> MagicMock:
    m = MagicMock()
    m.get_price.side_effect = lambda ticker: prices[ticker]
    return m


async def setup(db, balance: Decimal = Decimal("10000"), fee: Decimal = Decimal("0"), **kwargs):
    data = CompetitionCreate(
        name="Portfolio Test",
        starting_balance=balance,
        asset_universe=["AAPL", "BTC-USD"],
        fee_pct=fee,
        creator_name="Trader",
        **kwargs,
    )
    comp, player, _ = await create_competition(db, data)
    await start_competition(db, comp.lobby_code, player)
    return comp, player


@pytest.mark.asyncio
async def test_portfolio_no_positions(db):
    _, player = await setup(db)
    result = await get_portfolio(db, player, make_adapter(Decimal("100")))

    assert result.cash_balance == Decimal("10000")
    assert result.positions == []
    assert result.unrealized_pnl == Decimal("0")
    assert result.realized_pnl == Decimal("0")
    assert result.total_value == Decimal("10000")


@pytest.mark.asyncio
async def test_portfolio_unrealized_gain(db):
    comp, player = await setup(db)

    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    # Price rose to 150 — unrealized gain of 500
    result = await get_portfolio(db, player, make_adapter(Decimal("150")))

    assert len(result.positions) == 1
    pos = result.positions[0]
    assert pos.ticker == "AAPL"
    assert pos.quantity == Decimal("10")
    assert pos.avg_entry_price == Decimal("100")
    assert pos.current_price == Decimal("150")
    assert pos.market_value == Decimal("1500")
    assert pos.unrealized_pnl == Decimal("500")
    assert pos.side == "long"
    assert result.unrealized_pnl == Decimal("500")


@pytest.mark.asyncio
async def test_portfolio_unrealized_loss(db):
    comp, player = await setup(db)

    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    result = await get_portfolio(db, player, make_adapter(Decimal("80")))
    pos = result.positions[0]
    assert pos.unrealized_pnl == Decimal("-200")


@pytest.mark.asyncio
async def test_portfolio_total_value(db):
    comp, player = await setup(db, balance=Decimal("10000"))

    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    # Cash = 9000, position = 10 * 120 = 1200 → total = 10200
    result = await get_portfolio(db, player, make_adapter(Decimal("120")))
    assert result.cash_balance == Decimal("9000")
    assert result.positions_value == Decimal("1200")
    assert result.total_value == Decimal("10200")


# ── Multi-ticker portfolio ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_two_tickers_summed_correctly(db):
    """Buy AAPL and BTC-USD; portfolio aggregates both unrealized PnL values."""
    comp, player = await setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="BTC-USD", side=OrderSide.buy, quantity=Decimal("1")),
        make_adapter(Decimal("30000")),
    )

    result = await get_portfolio(
        db, player,
        make_multi_adapter({"AAPL": Decimal("120"), "BTC-USD": Decimal("35000")}),
    )

    assert len(result.positions) == 2
    aapl = next(p for p in result.positions if p.ticker == "AAPL")
    btc = next(p for p in result.positions if p.ticker == "BTC-USD")

    assert aapl.unrealized_pnl == Decimal("200")    # (120-100)*10
    assert btc.unrealized_pnl == Decimal("5000")    # (35000-30000)*1
    assert result.unrealized_pnl == Decimal("5200")


@pytest.mark.asyncio
async def test_portfolio_mixed_gain_and_loss(db):
    """One position gaining, one losing — unrealized PnL is the net."""
    comp, player = await setup(db, balance=Decimal("100000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="BTC-USD", side=OrderSide.buy, quantity=Decimal("1")),
        make_adapter(Decimal("30000")),
    )

    # AAPL up 200, BTC down 5000 → net = -4800
    result = await get_portfolio(
        db, player,
        make_multi_adapter({"AAPL": Decimal("120"), "BTC-USD": Decimal("25000")}),
    )
    assert result.unrealized_pnl == Decimal("-4800")


# ── Short positions ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_short_position_shows_negative_quantity(db):
    comp, player = await setup(db, balance=Decimal("50000"), allow_shorts=True)

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    result = await get_portfolio(db, player, make_adapter(Decimal("100")))
    assert len(result.positions) == 1
    pos = result.positions[0]
    assert pos.quantity == Decimal("-10")
    assert pos.side == "short"


@pytest.mark.asyncio
async def test_portfolio_short_unrealized_profit_when_price_falls(db):
    """Short 10 @ $100; price drops to $80 → unrealized PnL = +$200."""
    comp, player = await setup(db, balance=Decimal("50000"), allow_shorts=True)

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    result = await get_portfolio(db, player, make_adapter(Decimal("80")))
    pos = result.positions[0]
    assert pos.unrealized_pnl == Decimal("200")    # (100-80)*10
    assert result.unrealized_pnl == Decimal("200")


@pytest.mark.asyncio
async def test_portfolio_short_unrealized_loss_when_price_rises(db):
    """Short 10 @ $100; price rises to $120 → unrealized PnL = −$200."""
    comp, player = await setup(db, balance=Decimal("50000"), allow_shorts=True)

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    result = await get_portfolio(db, player, make_adapter(Decimal("120")))
    assert result.unrealized_pnl == Decimal("-200")


# ── Realized PnL in portfolio ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_realized_pnl_after_round_trip(db):
    """Buy 10 @ $100, sell 10 @ $130 → realized = $300, no open positions."""
    comp, player = await setup(db)

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("130")),
    )

    result = await get_portfolio(db, player, make_adapter(Decimal("130")))
    assert result.realized_pnl == Decimal("300")
    assert result.unrealized_pnl == Decimal("0")
    assert result.positions == []
    assert result.total_value == Decimal("10300")


# ── Portfolio HTTP endpoint ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portfolio_http_endpoint_returns_all_fields(client):
    create_resp = await client.post(
        "/competitions",
        json={
            "name": "Portfolio HTTP", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]
    player_id = body["player_id"]

    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})

    resp = await client.get(
        f"/players/{player_id}/portfolio",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    for field in ("cash_balance", "total_value", "positions"):
        assert field in data, f"Missing portfolio field: {field}"

    assert float(data["total_value"]) >= float(data["cash_balance"])
