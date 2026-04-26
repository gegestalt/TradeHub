from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide, OrderStatus
from schemas.competition import CompetitionCreate
from schemas.order import OrderCreate
from services.competition import create_competition, start_competition
from services.order_engine import place_order


def make_adapter(price: Decimal) -> MagicMock:
    adapter = MagicMock()
    adapter.get_price.return_value = price
    return adapter


async def setup_active_competition(
    db, balance: Decimal = Decimal("10000"), fee: Decimal = Decimal("0.001"), **kwargs
):
    from tests.conftest import make_user
    user, _ = await make_user(db, "Trader")
    data = CompetitionCreate(
        name="Test",
        starting_balance=balance,
        asset_universe=["AAPL", "BTC-USD"],
        fee_pct=fee,
        **kwargs,
    )
    comp, player, _ = await create_competition(db, data, user)
    await start_competition(db, comp.lobby_code, player)
    return comp, player


@pytest.mark.asyncio
async def test_market_buy_fills_immediately(db):
    comp, player = await setup_active_competition(db)
    order = await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("150.00")),
    )

    assert order.status == OrderStatus.filled
    assert order.fill_price == Decimal("150.00")
    assert order.fill_at is not None


@pytest.mark.asyncio
async def test_buy_deducts_cash_and_fee(db):
    comp, player = await setup_active_competition(db, fee=Decimal("0.001"))
    price = Decimal("150.00")
    qty = Decimal("10")

    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=qty),
        make_adapter(price),
    )

    fee = price * qty * Decimal("0.001")
    expected = Decimal("10000") - price * qty - fee
    assert player.cash_balance == expected


@pytest.mark.asyncio
async def test_fee_calculation(db):
    comp, player = await setup_active_competition(db, fee=Decimal("0.002"))
    price = Decimal("50000")
    qty = Decimal("0.1")

    order = await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="BTC-USD", side=OrderSide.buy, quantity=qty),
        make_adapter(price),
    )

    assert order.fee_paid == price * qty * Decimal("0.002")


@pytest.mark.asyncio
async def test_sell_after_buy_updates_cash(db):
    comp, player = await setup_active_competition(db, fee=Decimal("0"))
    adapter = make_adapter(Decimal("100.00"))

    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        adapter,
    )

    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5")),
        adapter,
    )

    # With 0% fee, selling back at same price should restore balance
    assert player.cash_balance == Decimal("10000")


@pytest.mark.asyncio
async def test_insufficient_balance_cancels_order(db):
    from fastapi import HTTPException

    comp, player = await setup_active_competition(db, balance=Decimal("100"), fee=Decimal("0"))

    with pytest.raises(HTTPException) as exc_info:
        await place_order(
            db,
            player,
            comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
            make_adapter(Decimal("200.00")),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_sell_without_position_fails(db):
    from fastapi import HTTPException

    comp, player = await setup_active_competition(db)

    with pytest.raises(HTTPException) as exc_info:
        await place_order(
            db,
            player,
            comp,
            OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5")),
            make_adapter(Decimal("100.00")),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_ticker_rejected(db):
    from fastapi import HTTPException

    comp, player = await setup_active_competition(db)

    with pytest.raises(HTTPException) as exc_info:
        await place_order(
            db,
            player,
            comp,
            OrderCreate(ticker="FAKE", side=OrderSide.buy, quantity=Decimal("1")),
            make_adapter(Decimal("10.00")),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_vwac_on_multiple_buys(db):
    comp, player = await setup_active_competition(db, balance=Decimal("100000"), fee=Decimal("0"))

    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("200")),
    )

    from sqlalchemy import select

    from models.position import Position

    result = await db.execute(
        select(Position).where(Position.player_id == player.id, Position.ticker == "AAPL")
    )
    pos = result.scalar_one()

    assert pos.quantity == Decimal("20")
    assert pos.avg_entry_price == Decimal("150")  # (100*10 + 200*10) / 20
