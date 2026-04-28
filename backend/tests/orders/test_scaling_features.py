"""Tests for the three production-readiness improvements.

1. Optimistic locking — balance_version increments on every fill; a stale-read
   conflict returns HTTP 409 instead of silently overwriting the balance.

2. DB-persisted audit log — every order fill, rejection, and position change
   writes a row to audit_log inside the same transaction.

3. Partial fills — when a market buy's total cost exceeds the player's balance,
   the engine fills as many shares as the balance allows rather than rejecting
   the whole order.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from models.audit_log import AuditLog
from models.enums import OrderSide, OrderStatus
from models.position import Position
from schemas.competition import CompetitionCreate
from schemas.order import OrderCreate
from services.competition import create_competition, join_competition, start_competition
from services.order_engine import place_order
from tests.conftest import create_lobby_http, make_user


def _adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(
    db,
    balance: Decimal = Decimal("1000"),
    fee: Decimal = Decimal("0"),
    max_leverage: Decimal = Decimal("1.0"),
):
    user, _ = await make_user(db, "Host")
    comp, player, _ = await create_competition(
        db,
        CompetitionCreate(
            name="Test", starting_balance=balance,
            asset_universe=["AAPL", "TSLA"],
            fee_pct=fee, max_leverage=max_leverage,
        ),
        user,
    )
    await start_competition(db, comp.lobby_code, player)
    return comp, player


# ── 1. Optimistic locking ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_balance_version_increments_on_fill(db):
    """balance_version must increase by 1 each time a fill commits."""
    comp, player = await _setup(db, balance=Decimal("10000"))
    await db.refresh(player)
    version_before = player.balance_version

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        _adapter(Decimal("100")),
    )
    await db.refresh(player)

    assert player.balance_version == version_before + 1


@pytest.mark.asyncio
async def test_balance_version_increments_per_fill(db):
    """Each successive fill increments balance_version by exactly 1."""
    comp, player = await _setup(db, balance=Decimal("10000"))
    await db.refresh(player)

    for _ in range(3):
        version_before = player.balance_version
        await place_order(
            db, player, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
            _adapter(Decimal("100")),
        )
        await db.refresh(player)
        assert player.balance_version == version_before + 1, (
            f"Expected version {version_before + 1}, got {player.balance_version}"
        )


@pytest.mark.asyncio
async def test_balance_version_unchanged_on_pending_order(db):
    """A resting limit order (pending, never fills) must not increment the version."""
    comp, player = await _setup(db, balance=Decimal("10000"))
    await db.refresh(player)
    version_before = player.balance_version

    # Limit price far below market — order stays pending
    order = await place_order(
        db, player, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy,
            order_type="limit", quantity=Decimal("1"), limit_price=Decimal("0.01"),
        ),
        _adapter(Decimal("100")),
    )
    await db.refresh(player)

    assert order.status == OrderStatus.pending
    assert player.balance_version == version_before


@pytest.mark.asyncio
async def test_optimistic_lock_409_error_path(db):
    """StaleDataError from db.flush() in execute_fill is converted to HTTP 409.

    StaleDataError occurs in production when two uvicorn workers both read the
    same balance_version, one commits first (version N→N+1), and the second
    worker's UPDATE WHERE version=N matches 0 rows.

    Since asyncio is single-threaded this cannot happen naturally in tests;
    we simulate it by patching db.flush to raise StaleDataError on the
    specific call that lives inside execute_fill's try/except block.
    """
    from datetime import datetime as dt
    from unittest.mock import patch
    from sqlalchemy.orm.exc import StaleDataError
    from fastapi import HTTPException
    from models.order import Order
    from services.order_engine import execute_fill

    comp, player = await _setup(db, balance=Decimal("10000"))

    # Build a minimal order already in the session
    order = Order(
        id="mock-order-stale",
        player_id=player.id, ticker="AAPL",
        order_type="market", side="buy",
        quantity=Decimal("1"),
        status="pending", fee_paid=Decimal("0"),
        time_in_force="gtc",
        created_at=dt.utcnow(),
    )
    db.add(order)
    await db.flush()

    # Intercept the flush INSIDE execute_fill (the one after the lock exits)
    original_flush = db.flush
    flush_calls = [0]

    async def intercepted_flush(*args, **kwargs):
        flush_calls[0] += 1
        # First flush = the one in place_order before execute_fill; let it pass.
        # Any subsequent flush = the one execute_fill uses for the StaleData check.
        if flush_calls[0] >= 1:
            raise StaleDataError("Simulated concurrent write from another worker")
        return await original_flush(*args, **kwargs)

    db.flush = intercepted_flush

    try:
        with pytest.raises(HTTPException) as exc_info:
            await execute_fill(db, player, comp, order, Decimal("100"))
    finally:
        db.flush = original_flush

    assert exc_info.value.status_code == 409
    assert "concurrent" in exc_info.value.detail.lower()


# ── 2. DB-persisted audit log ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_log_written_on_fill(db):
    """A filled order must create an order_fill row in audit_log."""
    comp, player = await _setup(db, balance=Decimal("1000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        _adapter(Decimal("100")),
    )
    await db.flush()

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.event_type == "order_fill")
    )).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.player_id == player.id
    assert row.competition_id == comp.id
    assert row.ticker == "AAPL"
    assert "fill_price" in row.payload
    assert "balance_before" in row.payload


@pytest.mark.asyncio
async def test_audit_log_written_on_rejection(db):
    """When balance is truly zero, a buy is rejected and an order_rejected row is written."""
    # max_leverage=100 lets the leverage check pass (notional 1000 <= 50*100=5000)
    # but affordable_qty=0 so the partial-fill path calls log_order_rejected.
    comp, player = await _setup(db, balance=Decimal("50"), max_leverage=Decimal("100"))

    with pytest.raises(Exception):
        await place_order(
            db, player, comp,
            # 10 shares at $100 = $1000 cost >> $50 balance → partial would be 0 → cancelled
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
            _adapter(Decimal("100")),
        )
    await db.flush()

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.event_type == "order_rejected")
    )).scalars().all()

    assert len(rows) == 1
    assert "Insufficient" in rows[0].payload


@pytest.mark.asyncio
async def test_audit_log_written_on_position_change(db):
    """Every fill writes at least one position_change audit row."""
    comp, player = await _setup(db, balance=Decimal("1000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("2")),
        _adapter(Decimal("100")),
    )
    await db.flush()

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.event_type == "position_change")
    )).scalars().all()

    assert len(rows) >= 1
    assert rows[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_audit_log_multiple_fills_multiple_rows(db):
    """Three fills produce three order_fill rows, each with the correct fill_price."""
    comp, player = await _setup(db, balance=Decimal("10000"))
    prices = [Decimal("100"), Decimal("110"), Decimal("120")]

    for p in prices:
        await place_order(
            db, player, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
            _adapter(p),
        )
    await db.flush()

    rows = (await db.execute(
        select(AuditLog)
        .where(AuditLog.event_type == "order_fill")
        .order_by(AuditLog.recorded_at)
    )).scalars().all()

    assert len(rows) == 3
    for row, expected_price in zip(rows, prices):
        assert str(expected_price) in row.payload


@pytest.mark.asyncio
async def test_audit_log_via_http(client, db):
    """End-to-end: a filled HTTP order produces an audit_log row in the DB."""
    ctx = await create_lobby_http(
        client, "Auditor",
        starting_balance="50000", asset_universe=["AAPL"],
        fee_pct="0", data_source="mock",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    r = await client.post(
        f"/competitions/{ctx['code']}/players/{player_id}/orders",
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    assert r.status_code == 201

    rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.event_type == "order_fill",
            AuditLog.player_id == player_id,
        )
    )).scalars().all()

    assert len(rows) == 1


# ── 3. Partial fills ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_partial_fill_when_balance_covers_fewer_shares(db):
    """Balance $350, price $100, order 10 → fills 3 shares (status=partial)."""
    # max_leverage=10 ensures the leverage check passes; the limit is the balance.
    comp, player = await _setup(
        db, balance=Decimal("350"), fee=Decimal("0"), max_leverage=Decimal("10")
    )

    order = await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        _adapter(Decimal("100")),
    )

    assert order.status == OrderStatus.partial
    assert order.quantity == Decimal("3")
    assert player.cash_balance == Decimal("50")  # 350 - 3*100


@pytest.mark.asyncio
async def test_partial_fill_deducts_correct_cost(db):
    """Partial fill deducts only the cost of the filled quantity."""
    comp, player = await _setup(
        db, balance=Decimal("250"), fee=Decimal("0"), max_leverage=Decimal("10")
    )

    order = await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        _adapter(Decimal("100")),
    )

    assert order.status == OrderStatus.partial
    assert order.quantity == Decimal("2")
    assert player.cash_balance == Decimal("50")  # 250 - 2*100


@pytest.mark.asyncio
async def test_partial_fill_zero_affordable_cancels(db):
    """If the player can afford 0 shares, the order is cancelled outright."""
    comp, player = await _setup(
        db, balance=Decimal("10"), fee=Decimal("0"), max_leverage=Decimal("10")
    )

    with pytest.raises(Exception):
        await place_order(
            db, player, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
            _adapter(Decimal("100")),
        )
    assert player.cash_balance == Decimal("10")  # unchanged


@pytest.mark.asyncio
async def test_partial_fill_creates_position(db):
    """A partially filled order creates a position for the filled quantity."""
    comp, player = await _setup(
        db, balance=Decimal("350"), fee=Decimal("0"), max_leverage=Decimal("10")
    )

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        _adapter(Decimal("100")),
    )
    await db.flush()

    pos = (await db.execute(
        select(Position).where(
            Position.player_id == player.id, Position.ticker == "AAPL"
        )
    )).scalar_one_or_none()

    assert pos is not None
    assert pos.quantity == Decimal("3")


@pytest.mark.asyncio
async def test_partial_fill_audit_log_event(db):
    """A partial fill writes an order_partial_fill row to audit_log."""
    comp, player = await _setup(
        db, balance=Decimal("350"), fee=Decimal("0"), max_leverage=Decimal("10")
    )

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        _adapter(Decimal("100")),
    )
    await db.flush()

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.event_type == "order_partial_fill")
    )).scalars().all()

    assert len(rows) == 1
    assert "requested_qty" in rows[0].payload
    assert "filled_qty" in rows[0].payload


@pytest.mark.asyncio
async def test_full_fill_when_balance_exactly_covers(db):
    """When balance exactly covers the full order, status is filled (not partial)."""
    comp, player = await _setup(db, balance=Decimal("300"), fee=Decimal("0"))

    order = await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("3")),
        _adapter(Decimal("100")),
    )

    assert order.status == OrderStatus.filled
    assert order.quantity == Decimal("3")
    assert player.cash_balance == Decimal("0")


@pytest.mark.asyncio
async def test_partial_fill_http_returns_partial_status(client):
    """HTTP endpoint returns status=partial when balance only covers part of the order."""
    from unittest.mock import patch

    mock_adapter = MagicMock()
    mock_adapter.get_price.return_value = Decimal("100")

    ctx = await create_lobby_http(
        client, "PartialBuyer",
        starting_balance="250",       # can afford 2 shares at $100
        asset_universe=["AAPL"],
        fee_pct="0",
        max_leverage="100",           # bypass leverage gate; let balance gate apply
        data_source="online",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    with patch("routers.orders.get_adapter", return_value=mock_adapter):
        r = await client.post(
            f"/competitions/{ctx['code']}/players/{player_id}/orders",
            json={"ticker": "AAPL", "side": "buy", "quantity": "10"},
            headers={"Authorization": f"Bearer {ctx['player_token']}"},
        )

    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "partial"
    assert int(data["quantity"]) == 2  # 250 // 100 = 2
