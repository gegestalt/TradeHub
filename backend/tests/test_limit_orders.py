"""Tests for limit, stop-loss, take-profit, OCO orders and the order processor."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from schemas.competition import CompetitionCreate
from schemas.order import OCOCreate, OrderCreate
from services.competition import create_competition, start_competition
from services.order_engine import place_oco_order, place_order, should_fill
from services.order_processor import process_pending_orders


def make_adapter(price: Decimal) -> MagicMock:
    adapter = MagicMock()
    adapter.get_price.return_value = price
    return adapter


async def setup(db, balance: Decimal = Decimal("10000"), fee: Decimal = Decimal("0")):
    data = CompetitionCreate(
        name="Test",
        starting_balance=balance,
        asset_universe=["AAPL", "BTC-USD"],
        fee_pct=fee,
        creator_name="Trader",
    )
    comp, player, _ = await create_competition(db, data)
    await start_competition(db, comp.lobby_code, player)
    return comp, player


# ── should_fill unit tests ────────────────────────────────────────────────────


def make_order(order_type, side, limit_price=None, stop_price=None, take_profit_price=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        order_type=order_type,
        side=side,
        limit_price=limit_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
    )


def test_limit_buy_fills_at_or_below():
    o = make_order(OrderType.limit, OrderSide.buy, limit_price=Decimal("100"))
    assert should_fill(o, Decimal("100"))
    assert should_fill(o, Decimal("99"))
    assert not should_fill(o, Decimal("101"))


def test_limit_sell_fills_at_or_above():
    o = make_order(OrderType.limit, OrderSide.sell, limit_price=Decimal("200"))
    assert should_fill(o, Decimal("200"))
    assert should_fill(o, Decimal("201"))
    assert not should_fill(o, Decimal("199"))


def test_stop_loss_sell_fills_at_or_below():
    o = make_order(OrderType.stop_loss, OrderSide.sell, stop_price=Decimal("90"))
    assert should_fill(o, Decimal("90"))
    assert should_fill(o, Decimal("85"))
    assert not should_fill(o, Decimal("91"))


def test_take_profit_sell_fills_at_or_above():
    o = make_order(OrderType.take_profit, OrderSide.sell, take_profit_price=Decimal("150"))
    assert should_fill(o, Decimal("150"))
    assert should_fill(o, Decimal("160"))
    assert not should_fill(o, Decimal("149"))


# ── Limit order integration tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_limit_buy_stays_pending_when_price_above(db):
    comp, player = await setup(db)
    order = await place_order(
        db,
        player,
        comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.buy,
            order_type=OrderType.limit,
            quantity=Decimal("5"),
            limit_price=Decimal("100"),
        ),
        make_adapter(Decimal("120")),  # current price above limit — should NOT fill
    )
    assert order.status == OrderStatus.pending
    assert player.cash_balance == Decimal("10000")  # balance unchanged


@pytest.mark.asyncio
async def test_limit_buy_fills_immediately_when_price_at_limit(db):
    comp, player = await setup(db)
    order = await place_order(
        db,
        player,
        comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.buy,
            order_type=OrderType.limit,
            quantity=Decimal("5"),
            limit_price=Decimal("100"),
        ),
        make_adapter(Decimal("100")),  # price exactly at limit
    )
    assert order.status == OrderStatus.filled
    assert order.fill_price == Decimal("100")


@pytest.mark.asyncio
async def test_limit_buy_ioc_expires_when_not_fillable(db):
    comp, player = await setup(db)
    order = await place_order(
        db,
        player,
        comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.buy,
            order_type=OrderType.limit,
            quantity=Decimal("5"),
            limit_price=Decimal("100"),
            time_in_force=TimeInForce.ioc,
        ),
        make_adapter(Decimal("120")),  # price above limit → can't fill IOC
    )
    assert order.status == OrderStatus.expired
    assert player.cash_balance == Decimal("10000")


# ── Background processor tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_processor_fills_pending_limit_order(db):
    comp, player = await setup(db)

    # Place a limit buy at 100; price is 120 → stays pending
    order = await place_order(
        db,
        player,
        comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.buy,
            order_type=OrderType.limit,
            quantity=Decimal("5"),
            limit_price=Decimal("100"),
        ),
        make_adapter(Decimal("120")),
    )
    assert order.status == OrderStatus.pending

    # Simulate price dropping to 95 by patching the adapter on the competition
    from unittest.mock import patch

    cheap_adapter = make_adapter(Decimal("95"))
    with patch("services.order_processor.get_adapter", return_value=cheap_adapter):
        filled = await process_pending_orders(db)

    assert filled == 1
    assert order.status == OrderStatus.filled
    assert order.fill_price == Decimal("100")  # fills at limit price, not market


@pytest.mark.asyncio
async def test_processor_fills_stop_loss(db):
    comp, player = await setup(db, balance=Decimal("10000"))

    # First buy some shares
    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )

    # Place a stop-loss at 80
    sl = await place_order(
        db,
        player,
        comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.sell,
            order_type=OrderType.stop_loss,
            quantity=Decimal("5"),
            stop_price=Decimal("80"),
        ),
        make_adapter(Decimal("100")),  # price is 100 → no trigger yet
    )
    assert sl.status == OrderStatus.pending

    from unittest.mock import patch

    crash_adapter = make_adapter(Decimal("75"))  # price crashes below stop
    with patch("services.order_processor.get_adapter", return_value=crash_adapter):
        filled = await process_pending_orders(db)

    assert filled == 1
    assert sl.status == OrderStatus.filled
    assert sl.fill_price == Decimal("75")  # stop-loss fills at market price


# ── OCO tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oco_cancel_sibling_on_fill(db):
    comp, player = await setup(db, balance=Decimal("10000"))

    # Buy 5 shares first
    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )

    # Place OCO: stop-loss at 80, take-profit at 130
    sl, tp = await place_oco_order(
        db,
        player,
        comp,
        OCOCreate(
            ticker="AAPL",
            side=OrderSide.sell,
            quantity=Decimal("5"),
            stop_price=Decimal("80"),
            take_profit_price=Decimal("130"),
        ),
        make_adapter(Decimal("100")),
    )
    assert sl.oco_pair_id == tp.oco_pair_id

    # Price rises to 140 — take-profit should fill, stop-loss should cancel
    from unittest.mock import patch

    moon_adapter = make_adapter(Decimal("140"))
    with patch("services.order_processor.get_adapter", return_value=moon_adapter):
        filled = await process_pending_orders(db)

    assert filled == 1
    assert tp.status == OrderStatus.filled
    assert sl.status == OrderStatus.cancelled  # sibling cancelled


# ── Realized P&L tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_realized_pnl_on_sell(db):
    comp, player = await setup(db, balance=Decimal("10000"), fee=Decimal("0"))

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
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("150")),  # sold at 150, bought at 100 → +500 realized
    )

    assert player.realized_pnl == Decimal("500")


@pytest.mark.asyncio
async def test_realized_pnl_loss(db):
    comp, player = await setup(db, balance=Decimal("10000"), fee=Decimal("0"))

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
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("80")),  # sold at 80, bought at 100 → -200 realized
    )

    assert player.realized_pnl == Decimal("-200")
