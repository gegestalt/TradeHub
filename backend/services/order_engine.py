import time
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import audit
from data_adapters.base import DataAdapter
from locks import get_player_lock
from metrics import metrics
from models.competition import Competition
from models.enums import CompetitionState, OrderSide, OrderStatus, OrderType
from models.order import Order
from models.player import Player
from models.position import Position
from schemas.order import OrderCreate

_MONETARY_DP = Decimal("0.00000001")


# ── Pure calculation functions (tested independently via test_properties.py) ──

def _q(value: Decimal) -> Decimal:
    return value.quantize(_MONETARY_DP, rounding=ROUND_HALF_UP)


def calculate_fee(price: Decimal, quantity: Decimal, fee_pct: Decimal) -> Decimal:
    return _q(price * quantity * fee_pct)


def calculate_buy_cost(price: Decimal, quantity: Decimal, fee_pct: Decimal) -> Decimal:
    """Total cash deducted on a buy: gross + fee."""
    return _q(price * quantity + calculate_fee(price, quantity, fee_pct))


def calculate_sell_proceeds(price: Decimal, quantity: Decimal, fee_pct: Decimal) -> Decimal:
    """Net cash received on a sell: gross − fee."""
    return _q(price * quantity - calculate_fee(price, quantity, fee_pct))


def calculate_vwac(
    existing_qty: Decimal,
    existing_price: Decimal,
    new_qty: Decimal,
    new_price: Decimal,
) -> Decimal:
    """Volume-weighted average cost of two positions combined."""
    return _q(
        (existing_price * existing_qty + new_price * new_qty) / (existing_qty + new_qty)
    )


# ── Order entry ───────────────────────────────────────────────────────────────

async def place_order(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    data: OrderCreate,
    adapter: DataAdapter,
) -> Order:
    t0 = time.perf_counter()
    filled = False
    try:
        ticker = data.ticker.upper()

        if ticker not in [t.upper() for t in competition.asset_universe]:
            raise HTTPException(
                status_code=400,
                detail=f"Ticker '{ticker}' is not in the competition's asset universe",
            )

        if competition.state != CompetitionState.active:
            raise HTTPException(status_code=400, detail="Competition is not active")

        price = adapter.get_price(ticker)

        order = Order(
            player_id=player.id,
            ticker=ticker,
            order_type=data.order_type,
            side=data.side,
            quantity=_q(data.quantity),
            limit_price=data.limit_price,
            stop_price=data.stop_price,
            status=OrderStatus.pending,
        )
        db.add(order)
        await db.flush()

        if data.order_type == OrderType.market:
            await _fill_market_order(db, player, competition, order, price)
            filled = order.status == OrderStatus.filled

        return order
    finally:
        metrics.record_order(time.perf_counter() - t0, filled=filled)


async def cancel_order(db: AsyncSession, player: Player, order_id: str) -> Order:
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.player_id == player.id)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != OrderStatus.pending:
        raise HTTPException(
            status_code=400, detail=f"Cannot cancel an order with status '{order.status}'"
        )

    order.status = OrderStatus.cancelled
    return order


# ── Fill logic ────────────────────────────────────────────────────────────────

async def _fill_market_order(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
) -> None:
    # Per-player lock: prevents balance/position races under concurrent requests
    # within a single process. For multi-process PostgreSQL, add SELECT FOR UPDATE.
    async with get_player_lock(player.id):
        balance_before = player.cash_balance
        fee = calculate_fee(price, order.quantity, competition.fee_pct)

        if order.side == OrderSide.buy:
            cost = calculate_buy_cost(price, order.quantity, competition.fee_pct)
            if player.cash_balance < cost:
                order.status = OrderStatus.cancelled
                audit.log_order_rejected(
                    player_id=player.id,
                    order_id=order.id,
                    ticker=order.ticker,
                    reason=f"Insufficient balance: have {player.cash_balance}, need {cost}",
                )
                raise HTTPException(status_code=400, detail="Insufficient cash balance")

            player.cash_balance = _q(player.cash_balance - cost)
            await _update_position(db, player.id, order.ticker, order.quantity, price)

        else:  # sell / open short
            pos = await _get_position(db, player.id, order.ticker)
            available = pos.quantity if pos else Decimal("0")

            if not competition.allow_shorts and available < order.quantity:
                order.status = OrderStatus.cancelled
                audit.log_order_rejected(
                    player_id=player.id,
                    order_id=order.id,
                    ticker=order.ticker,
                    reason=f"Insufficient position: have {available}, need {order.quantity}",
                )
                raise HTTPException(status_code=400, detail="Insufficient position quantity to sell")

            proceeds = calculate_sell_proceeds(price, order.quantity, competition.fee_pct)
            player.cash_balance = _q(player.cash_balance + proceeds)
            await _update_position(db, player.id, order.ticker, -order.quantity, price)

        order.fill_price = price
        order.fill_at = datetime.utcnow()
        order.fee_paid = fee
        order.status = OrderStatus.filled

        audit.log_order_fill(
            player_id=player.id,
            competition_id=competition.id,
            order_id=order.id,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            fill_price=price,
            fee=fee,
            balance_before=balance_before,
            balance_after=player.cash_balance,
        )


# ── Position helpers ──────────────────────────────────────────────────────────

async def _get_position(db: AsyncSession, player_id: str, ticker: str) -> Position | None:
    result = await db.execute(
        select(Position).where(Position.player_id == player_id, Position.ticker == ticker)
    )
    return result.scalar_one_or_none()


async def _update_position(
    db: AsyncSession,
    player_id: str,
    ticker: str,
    quantity_delta: Decimal,
    price: Decimal,
) -> None:
    pos = await _get_position(db, player_id, ticker)

    if pos is None:
        qty = _q(quantity_delta)
        db.add(Position(player_id=player_id, ticker=ticker, quantity=qty, avg_entry_price=_q(price)))
        audit.log_position_change(
            player_id=player_id, ticker=ticker,
            qty_before=Decimal("0"), qty_after=qty, avg_entry_price=_q(price),
        )
        return

    qty_before = pos.quantity
    new_qty = _q(pos.quantity + quantity_delta)

    if new_qty == Decimal("0"):
        await db.delete(pos)
        audit.log_position_change(
            player_id=player_id, ticker=ticker,
            qty_before=qty_before, qty_after=Decimal("0"), avg_entry_price=pos.avg_entry_price,
        )
        return

    # Recalculate VWAC only when adding to an existing directional position
    if quantity_delta > 0 and pos.quantity > 0:
        pos.avg_entry_price = calculate_vwac(pos.quantity, pos.avg_entry_price, quantity_delta, price)
    elif quantity_delta < 0 and pos.quantity < 0:
        abs_existing, abs_delta = abs(pos.quantity), abs(quantity_delta)
        pos.avg_entry_price = calculate_vwac(abs_existing, pos.avg_entry_price, abs_delta, price)

    pos.quantity = new_qty
    audit.log_position_change(
        player_id=player_id, ticker=ticker,
        qty_before=qty_before, qty_after=new_qty, avg_entry_price=pos.avg_entry_price,
    )
