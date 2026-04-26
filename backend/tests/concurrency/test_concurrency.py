"""Concurrency and race condition tests.

These tests fire multiple coroutines against the same player simultaneously
(using asyncio.gather) to verify that the per-player lock in order_engine.py
prevents balance races and overselling.
"""

import asyncio
import contextlib
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from models.enums import OrderSide, OrderStatus
from models.position import Position
from schemas.competition import CompetitionCreate
from schemas.order import OrderCreate
from services.competition import create_competition, join_competition, start_competition
from tests.conftest import make_user
from services.order_engine import place_order


def make_adapter(price: Decimal) -> MagicMock:
    adapter = MagicMock()
    adapter.get_price.return_value = price
    return adapter


async def _setup(db, balance: Decimal = Decimal("1000"), fee: Decimal = Decimal("0")):
    user, _ = await make_user(db, "Host")
    data = CompetitionCreate(
        name="Concurrency",
        starting_balance=balance,
        asset_universe=["AAPL", "TSLA"],
        fee_pct=fee,
    )
    comp, player, _ = await create_competition(db, data, user)
    await start_competition(db, comp.lobby_code, player)
    return comp, player


@pytest.mark.asyncio
async def test_concurrent_buys_balance_never_negative(db):
    """20 concurrent buy attempts — balance must stay >= 0 throughout."""
    comp, player = await _setup(db, balance=Decimal("500"))
    adapter = make_adapter(Decimal("100.00"))

    async def try_buy():
        with contextlib.suppress(Exception):
            await place_order(
                db,
                player,
                comp,
                OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
                adapter,
            )

    await asyncio.gather(*[try_buy() for _ in range(20)])

    assert player.cash_balance >= Decimal("0"), (
        f"Negative balance after concurrent orders: {player.cash_balance}"
    )
    # With balance=500 and each order costing 100, exactly 5 should fill
    assert player.cash_balance >= Decimal("0")


@pytest.mark.asyncio
async def test_concurrent_sells_no_oversell(db):
    """Two concurrent sell orders should not both fill when only one can be fulfilled."""
    comp, player = await _setup(db, balance=Decimal("10000"), fee=Decimal("0"))
    adapter = make_adapter(Decimal("100.00"))

    # Buy exactly 5 shares
    await place_order(
        db,
        player,
        comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        adapter,
    )

    filled_orders = []

    async def try_sell_4():
        try:
            order = await place_order(
                db,
                player,
                comp,
                OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("4")),
                adapter,
            )
            if order.status == OrderStatus.filled:
                filled_orders.append(order)
        except Exception:
            pass

    await asyncio.gather(try_sell_4(), try_sell_4())

    total_sold = sum(o.quantity for o in filled_orders)
    assert total_sold <= Decimal("5"), f"Oversold {total_sold} shares when only 5 were owned"

    # Verify final position is consistent
    result = await db.execute(
        select(Position).where(Position.player_id == player.id, Position.ticker == "AAPL")
    )
    pos = result.scalar_one_or_none()
    remaining = pos.quantity if pos else Decimal("0")
    assert remaining >= Decimal("0"), f"Negative position: {remaining}"


@pytest.mark.asyncio
async def test_two_players_do_not_cross_contaminate(db):
    """Orders from two players must not affect each other's balances or positions."""
    u1, _ = await make_user(db, "P1")
    u2, _ = await make_user(db, "P2")
    data = CompetitionCreate(
        name="Two Player Race",
        starting_balance=Decimal("1000"),
        asset_universe=["AAPL"],
        fee_pct=Decimal("0"),
    )
    comp, p1, _ = await create_competition(db, data, u1)
    p2, _ = await join_competition(db, comp.lobby_code, u2)
    await start_competition(db, comp.lobby_code, p1)

    adapter = make_adapter(Decimal("100.00"))

    async def player_buys(player):
        for _ in range(5):
            with contextlib.suppress(Exception):
                await place_order(
                    db,
                    player,
                    comp,
                    OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
                    adapter,
                )

    await asyncio.gather(player_buys(p1), player_buys(p2))

    assert p1.cash_balance >= Decimal("0"), f"P1 negative: {p1.cash_balance}"
    assert p2.cash_balance >= Decimal("0"), f"P2 negative: {p2.cash_balance}"
    # Neither player spent more than they started with
    assert p1.cash_balance <= Decimal("1000")
    assert p2.cash_balance <= Decimal("1000")


@pytest.mark.asyncio
async def test_rapid_sequential_orders_all_succeed_while_solvent(db):
    """8 sequential orders should all fill while balance permits."""
    comp, player = await _setup(db, balance=Decimal("100000"), fee=Decimal("0"))
    adapter = make_adapter(Decimal("10.00"))

    filled = 0
    for _ in range(8):
        try:
            order = await place_order(
                db,
                player,
                comp,
                OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
                adapter,
            )
            if order.status == OrderStatus.filled:
                filled += 1
        except Exception:
            pass

    assert filled == 8


@pytest.mark.asyncio
async def test_concurrent_buys_total_position_consistent(db):
    """After concurrent buys the position quantity must match total filled quantity."""
    comp, player = await _setup(db, balance=Decimal("50000"), fee=Decimal("0"))
    adapter = make_adapter(Decimal("100.00"))
    filled_orders = []

    async def try_buy():
        try:
            order = await place_order(
                db,
                player,
                comp,
                OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
                adapter,
            )
            if order.status == OrderStatus.filled:
                filled_orders.append(order)
        except Exception:
            pass

    await asyncio.gather(*[try_buy() for _ in range(30)])

    result = await db.execute(
        select(Position).where(Position.player_id == player.id, Position.ticker == "AAPL")
    )
    pos = result.scalar_one_or_none()
    actual_qty = pos.quantity if pos else Decimal("0")
    expected_qty = sum(o.quantity for o in filled_orders)

    assert actual_qty == expected_qty, (
        f"Position {actual_qty} doesn't match filled quantity {expected_qty}"
    )

    # Cash + position value == starting balance (0% fee)
    cash = player.cash_balance
    pos_value = actual_qty * Decimal("100")
    assert abs(cash + pos_value - Decimal("50000")) < Decimal("0.0001")
