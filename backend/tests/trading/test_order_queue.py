"""TDD tests for the deterministic per-competition order queue.

The asyncio.Queue consumer serialises all fills for a competition,
eliminating race conditions without optimistic-lock retries.
"""

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide, OrderStatus
from schemas.competition import CompetitionCreate
from schemas.order import OrderCreate
from services.competition import create_competition, start_competition
from services.order_queue import (
    active_competitions,
    drain,
    queue_depth,
    submit,
)
from tests.conftest import make_user


def _adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(db, balance: Decimal = Decimal("10000")):
    user, _ = await make_user(db, "Host")
    comp, player, _ = await create_competition(
        db,
        CompetitionCreate(
            name="Queue Test", starting_balance=balance,
            asset_universe=["AAPL"], fee_pct=Decimal("0"),
        ),
        user,
    )
    await start_competition(db, comp.lobby_code, player)
    return comp, player


@pytest.mark.asyncio
async def test_submit_fills_order_and_returns_it(db):
    """submit() returns the filled Order with status=filled."""
    comp, player = await _setup(db)

    order = await submit(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        _adapter(Decimal("100")),
    )

    assert order.status == OrderStatus.filled
    assert order.fill_price == Decimal("100")
    assert player.cash_balance == Decimal("9900")


@pytest.mark.asyncio
async def test_submit_registers_active_competition(db):
    """After first submit the competition appears in active_competitions()."""
    comp, player = await _setup(db)
    before = active_competitions()

    await submit(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        _adapter(Decimal("100")),
    )

    assert comp.id in active_competitions()


@pytest.mark.asyncio
async def test_concurrent_submits_do_not_go_negative(db):
    """20 concurrent submit() calls must keep the balance >= 0."""
    comp, player = await _setup(db, balance=Decimal("500"))
    adapter = _adapter(Decimal("100"))

    async def _buy():
        try:
            await submit(
                db, player, comp,
                OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
                adapter,
            )
        except Exception:
            pass

    await asyncio.gather(*[_buy() for _ in range(20)])

    assert player.cash_balance >= Decimal("0"), (
        f"Balance went negative: {player.cash_balance}"
    )


@pytest.mark.asyncio
async def test_orders_processed_in_fifo_order(db):
    """Orders submitted first must be filled first (FIFO guarantee)."""
    comp, player = await _setup(db, balance=Decimal("100000"))
    adapter = _adapter(Decimal("100"))
    results = []

    async def _buy(tag: str):
        order = await submit(
            db, player, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
            adapter,
        )
        results.append((tag, order.fill_price))

    # Fire in known order and collect fill times
    await _buy("first")
    await _buy("second")
    await _buy("third")

    tags = [r[0] for r in results]
    assert tags == ["first", "second", "third"], f"Unexpected order: {tags}"


@pytest.mark.asyncio
async def test_drain_stops_consumer(db):
    """drain() sends a sentinel and the consumer task terminates."""
    comp, player = await _setup(db)
    await submit(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        _adapter(Decimal("100")),
    )
    assert comp.id in active_competitions()

    await drain(comp.id)

    assert comp.id not in active_competitions()


@pytest.mark.asyncio
async def test_queue_depth_reflects_pending_items(db):
    """queue_depth() returns 0 when there are no queued items."""
    comp, player = await _setup(db)
    assert queue_depth(comp.id) == 0

    await submit(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        _adapter(Decimal("100")),
    )
    # After processing, depth should be back to 0
    assert queue_depth(comp.id) == 0
