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


async def setup(db, balance: Decimal = Decimal("10000"), fee: Decimal = Decimal("0")):
    data = CompetitionCreate(
        name="Portfolio Test",
        starting_balance=balance,
        asset_universe=["AAPL", "BTC-USD"],
        fee_pct=fee,
        creator_name="Trader",
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
