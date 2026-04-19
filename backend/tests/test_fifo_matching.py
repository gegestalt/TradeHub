"""Tests for FIFO limit-order matching between players."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide, OrderStatus, OrderType
from schemas.competition import CompetitionCreate, JoinRequest
from schemas.order import OrderCreate
from services.competition import create_competition, join_competition, start_competition
from services.order_engine import place_order


def make_adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(db, num_extra_players: int = 1):
    """Create a started competition with creator Alice + `num_extra_players` more."""
    data = CompetitionCreate(
        name="FIFO Test",
        starting_balance=Decimal("10000"),
        asset_universe=["AAPL"],
        fee_pct=Decimal("0"),
        creator_name="Alice",
    )
    comp, alice, _ = await create_competition(db, data)
    extras = []
    names = ["Bob", "Charlie", "Dave"]
    for i in range(num_extra_players):
        p, _ = await join_competition(db, comp.lobby_code, JoinRequest(display_name=names[i]))
        extras.append(p)
    await start_competition(db, comp.lobby_code, alice)

    # Reload competition so players sub-select sees all rows
    from sqlalchemy import select as sa_select

    from models.competition import Competition

    result = await db.execute(sa_select(Competition).where(Competition.id == comp.id))
    comp = result.scalar_one()
    return (comp, alice, *extras)


@pytest.mark.asyncio
async def test_fifo_cross_fill(db):
    """Bob's resting sell crosses Alice's incoming buy — both fill at resting price."""
    comp, alice, bob = await _setup(db)
    adapter_95 = make_adapter(Decimal("95"))
    adapter_100 = make_adapter(Decimal("100"))

    # Bob buys AAPL first so he has inventory to sell
    await place_order(
        db, bob, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        adapter_95,
    )

    # Bob places a sell limit at 100; market is 95 so it stays pending
    bob_sell = await place_order(
        db, bob, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("100"),
        ),
        adapter_95,
    )
    assert bob_sell.status == OrderStatus.pending

    # Alice places a buy limit at 100; FIFO matches Bob's resting sell before market
    alice_buy = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("100"),
        ),
        adapter_100,  # market also at 100, but FIFO fires first
    )

    assert alice_buy.status == OrderStatus.filled
    assert alice_buy.fill_price == Decimal("100")
    assert bob_sell.status == OrderStatus.filled
    assert bob_sell.fill_price == Decimal("100")


@pytest.mark.asyncio
async def test_fifo_no_cross_when_prices_dont_match(db):
    """Alice's buy at 100 does not cross Bob's resting sell at 110."""
    comp, alice, bob = await _setup(db)
    adapter_95 = make_adapter(Decimal("95"))

    await place_order(
        db, bob, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        adapter_95,
    )
    bob_sell = await place_order(
        db, bob, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("110"),
        ),
        adapter_95,  # market 95 < 110, stays pending
    )
    assert bob_sell.status == OrderStatus.pending

    # Alice buys limit at 100; no FIFO match (110 > 100)
    await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("100"),
        ),
        adapter_95,  # market 95 ≤ 100 → fills at market, but NOT via FIFO
    )

    # Bob's sell at 110 was never touched
    assert bob_sell.status == OrderStatus.pending


@pytest.mark.asyncio
async def test_fifo_oldest_resting_order_fills_first(db):
    """Among two resting sells at the same price, the older one fills first."""
    comp, alice, bob, charlie = await _setup(db, num_extra_players=2)
    adapter_95 = make_adapter(Decimal("95"))

    # Both Bob and Charlie buy AAPL then place resting sells at 100
    await place_order(
        db, bob, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        adapter_95,
    )
    bob_sell = await place_order(
        db, bob, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("100"),
        ),
        adapter_95,
    )

    await place_order(
        db, charlie, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        adapter_95,
    )
    charlie_sell = await place_order(
        db, charlie, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("100"),
        ),
        adapter_95,
    )

    # Alice buys at 100 → matches Bob (oldest)
    alice_buy = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("100"),
        ),
        make_adapter(Decimal("100")),
    )

    assert alice_buy.status == OrderStatus.filled
    assert bob_sell.status == OrderStatus.filled
    assert charlie_sell.status == OrderStatus.pending  # Charlie was newer, stays resting


@pytest.mark.asyncio
async def test_fifo_no_self_match(db):
    """A player's own resting sell is excluded from FIFO matching for their buy."""
    comp, alice, bob = await _setup(db)
    adapter_95 = make_adapter(Decimal("95"))

    # Alice buys AAPL then places a resting sell at 100
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        adapter_95,
    )
    alice_sell = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("100"),
        ),
        adapter_95,
    )
    assert alice_sell.status == OrderStatus.pending

    # Alice places a buy limit at 100 — her own sell must NOT be the FIFO match
    alice_buy = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5"),
            order_type=OrderType.limit, limit_price=Decimal("100"),
        ),
        adapter_95,  # market 95 ≤ 100 → fills at market, NOT via self-match
    )

    # Buy fills at limit price (not via self-match); resting sell remains pending
    assert alice_buy.status == OrderStatus.filled
    assert alice_sell.status == OrderStatus.pending  # self-match was prevented
