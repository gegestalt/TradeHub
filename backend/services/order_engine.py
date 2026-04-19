import time
import uuid
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
from models.enums import CompetitionState, OrderSide, OrderStatus, OrderType, TimeInForce
from models.order import Order
from models.player import Player
from models.position import Position
from schemas.order import OCOCreate, OrderCreate

_MONETARY_DP = Decimal("0.00000001")


# ── Pure calculation functions ────────────────────────────────────────────────


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
    return _q((existing_price * existing_qty + new_price * new_qty) / (existing_qty + new_qty))


def should_fill(order: Order, current_price: Decimal) -> bool:
    """Return True if the current price satisfies the order's fill condition."""
    t = order.order_type
    s = order.side

    if t == OrderType.limit:
        # Limit buy fills when price drops to or below limit_price
        # Limit sell fills when price rises to or above limit_price
        if s == OrderSide.buy:
            return order.limit_price is not None and current_price <= order.limit_price
        return order.limit_price is not None and current_price >= order.limit_price

    if t == OrderType.stop_loss:
        # Stop-loss sell (exit long): triggers when price drops to stop_price
        # Stop-loss buy (cover short): triggers when price rises to stop_price
        if s == OrderSide.sell:
            return order.stop_price is not None and current_price <= order.stop_price
        return order.stop_price is not None and current_price >= order.stop_price

    if t == OrderType.take_profit:
        # Take-profit sell (exit long): triggers when price rises to take_profit_price
        # Take-profit buy (cover short): triggers when price drops to take_profit_price
        if s == OrderSide.sell:
            return order.take_profit_price is not None and current_price >= order.take_profit_price
        return order.take_profit_price is not None and current_price <= order.take_profit_price

    if t == OrderType.stop_limit:
        # Triggered at stop_price, executes at limit_price (or better)
        if s == OrderSide.sell:
            triggered = order.stop_price is not None and current_price <= order.stop_price
            at_limit = order.limit_price is not None and current_price >= order.limit_price
            return triggered and at_limit
        triggered = order.stop_price is not None and current_price >= order.stop_price
        at_limit = order.limit_price is not None and current_price <= order.limit_price
        return triggered and at_limit

    return False


def fill_price_for(order: Order, current_price: Decimal) -> Decimal:
    """Return the execution price for a non-market order."""
    t = order.order_type
    if t == OrderType.limit:
        return order.limit_price  # type: ignore[return-value]
    if t == OrderType.stop_loss:
        return current_price  # stops execute at market
    if t == OrderType.take_profit:
        return order.take_profit_price  # type: ignore[return-value]
    if t == OrderType.stop_limit:
        return order.limit_price  # type: ignore[return-value]
    return current_price


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

        current_price = adapter.get_price(ticker)

        order = Order(
            player_id=player.id,
            ticker=ticker,
            order_type=data.order_type,
            side=data.side,
            quantity=_q(data.quantity),
            limit_price=data.limit_price,
            stop_price=data.stop_price,
            take_profit_price=data.take_profit_price,
            time_in_force=data.time_in_force,
            status=OrderStatus.pending,
        )
        db.add(order)
        await db.flush()

        if data.order_type == OrderType.market:
            await _execute_fill(db, player, competition, order, current_price)
            filled = order.status == OrderStatus.filled
        elif data.order_type == OrderType.limit:
            # FIFO matching: try to cross against an existing resting limit order first
            matched = await _find_fifo_match(db, competition, order)
            if matched:
                exec_price = matched.limit_price  # type: ignore[assignment]
                matched_player_result = await db.execute(
                    select(Player).where(Player.id == matched.player_id)
                )
                matched_player = matched_player_result.scalar_one()
                await _execute_fill(db, matched_player, competition, matched, exec_price)
                await _execute_fill(db, player, competition, order, exec_price)
                filled = order.status == OrderStatus.filled
            elif should_fill(order, current_price):
                exec_price = fill_price_for(order, current_price)
                await _execute_fill(db, player, competition, order, exec_price)
                filled = order.status == OrderStatus.filled
            elif data.time_in_force in (TimeInForce.ioc, TimeInForce.fok):
                order.status = OrderStatus.expired
            # else: GTC limit stays pending for background processor
        elif should_fill(order, current_price):
            # Price already satisfies the condition → fill immediately for any TIF
            exec_price = fill_price_for(order, current_price)
            await _execute_fill(db, player, competition, order, exec_price)
            filled = order.status == OrderStatus.filled
        elif data.time_in_force in (TimeInForce.ioc, TimeInForce.fok):
            # Can't fill now and not GTC → expire immediately
            order.status = OrderStatus.expired
        # GTC conditional orders stay pending; the background processor fills them later

        return order
    finally:
        metrics.record_order(time.perf_counter() - t0, filled=filled)


async def place_oco_order(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    data: OCOCreate,
    adapter: DataAdapter,
) -> tuple[Order, Order]:
    """Place an OCO pair: a stop-loss and a take-profit on the same position."""
    ticker = data.ticker.upper()

    if ticker not in [t.upper() for t in competition.asset_universe]:
        raise HTTPException(
            status_code=400,
            detail=f"Ticker '{ticker}' is not in the competition's asset universe",
        )

    if competition.state != CompetitionState.active:
        raise HTTPException(status_code=400, detail="Competition is not active")

    pair_id = str(uuid.uuid4())

    sl_order = Order(
        player_id=player.id,
        ticker=ticker,
        order_type=OrderType.stop_loss,
        side=data.side,
        quantity=_q(data.quantity),
        stop_price=data.stop_price,
        time_in_force=TimeInForce.gtc,
        oco_pair_id=pair_id,
        status=OrderStatus.pending,
    )
    tp_order = Order(
        player_id=player.id,
        ticker=ticker,
        order_type=OrderType.take_profit,
        side=data.side,
        quantity=_q(data.quantity),
        take_profit_price=data.take_profit_price,
        time_in_force=TimeInForce.gtc,
        oco_pair_id=pair_id,
        status=OrderStatus.pending,
    )
    db.add(sl_order)
    db.add(tp_order)
    await db.flush()
    return sl_order, tp_order


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

    # Cancel the paired OCO leg too
    if order.oco_pair_id:
        pair_result = await db.execute(
            select(Order).where(
                Order.oco_pair_id == order.oco_pair_id,
                Order.id != order.id,
                Order.status == OrderStatus.pending,
            )
        )
        sibling = pair_result.scalar_one_or_none()
        if sibling:
            sibling.status = OrderStatus.cancelled

    return order


# ── FIFO matching ─────────────────────────────────────────────────────────────


async def _find_fifo_match(
    db: AsyncSession,
    competition: Competition,
    incoming: Order,
) -> Order | None:
    """Return the oldest resting limit order that crosses with `incoming`, or None.

    A buy order crosses a resting sell when buy.limit_price >= ask.limit_price.
    A sell order crosses a resting buy when sell.limit_price <= bid.limit_price.
    Only matches orders from other players in the same competition.
    """
    if incoming.limit_price is None:
        return None

    opposite_side = OrderSide.sell if incoming.side == OrderSide.buy else OrderSide.buy

    if incoming.side == OrderSide.buy:
        price_condition = Order.limit_price <= incoming.limit_price
    else:
        price_condition = Order.limit_price >= incoming.limit_price

    # Use a sub-select to scope to competition players without lazy-loading
    player_subq = select(Player.id).where(Player.competition_id == competition.id).scalar_subquery()

    result = await db.execute(
        select(Order)
        .where(
            Order.ticker == incoming.ticker,
            Order.order_type == OrderType.limit,
            Order.side == opposite_side,
            Order.status == OrderStatus.pending,
            Order.player_id != incoming.player_id,
            Order.player_id.in_(player_subq),
            price_condition,
        )
        .order_by(Order.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Fill logic ────────────────────────────────────────────────────────────────


async def _execute_fill(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
) -> None:
    """Execute a fill for any order type. Holds the per-player lock."""
    # no_autoflush prevents SAWarning when db.add() is called inside a nested select
    async with get_player_lock(player.id):
        with db.no_autoflush:
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
                await _update_position(db, player, order.ticker, order.quantity, price, fee)

            else:  # sell / open short
                pos = await _get_position(db, player.id, order.ticker)
                available = pos.quantity if pos else Decimal("0")

                if not competition.allow_shorts and available < order.quantity:
                    order.status = OrderStatus.cancelled
                    audit.log_order_rejected(
                        player_id=player.id,
                        order_id=order.id,
                        ticker=order.ticker,
                        reason=(f"Insufficient position: have {available}, need {order.quantity}"),
                    )
                    raise HTTPException(
                        status_code=400, detail="Insufficient position quantity to sell"
                    )

                proceeds = calculate_sell_proceeds(price, order.quantity, competition.fee_pct)
                player.cash_balance = _q(player.cash_balance + proceeds)

                # Realized P&L on closing a long position
                if pos and pos.quantity > 0:
                    qty_closed = min(order.quantity, pos.quantity)
                    cost_basis = pos.avg_entry_price * qty_closed
                    gross_revenue = price * qty_closed
                    close_fee = calculate_fee(price, qty_closed, competition.fee_pct)
                    player.realized_pnl = _q(
                        player.realized_pnl + gross_revenue - cost_basis - close_fee
                    )

                await _update_position(db, player, order.ticker, -order.quantity, price, fee)

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

            # Cancel the other OCO leg
            if order.oco_pair_id:
                pair_result = await db.execute(
                    select(Order).where(
                        Order.oco_pair_id == order.oco_pair_id,
                        Order.id != order.id,
                        Order.status == OrderStatus.pending,
                    )
                )
                sibling = pair_result.scalar_one_or_none()
                if sibling:
                    sibling.status = OrderStatus.cancelled


# ── Position helpers ──────────────────────────────────────────────────────────


async def _get_position(db: AsyncSession, player_id: str, ticker: str) -> Position | None:
    result = await db.execute(
        select(Position).where(Position.player_id == player_id, Position.ticker == ticker)
    )
    return result.scalar_one_or_none()


async def _update_position(
    db: AsyncSession,
    player: Player,
    ticker: str,
    quantity_delta: Decimal,
    price: Decimal,
    fee: Decimal,
) -> None:
    pos = await _get_position(db, player.id, ticker)

    if pos is None:
        qty = _q(quantity_delta)
        new_pos = Position(
            player_id=player.id, ticker=ticker, quantity=qty, avg_entry_price=_q(price)
        )
        db.add(new_pos)
        audit.log_position_change(
            player_id=player.id,
            ticker=ticker,
            qty_before=Decimal("0"),
            qty_after=qty,
            avg_entry_price=_q(price),
        )
        return

    qty_before = pos.quantity
    new_qty = _q(pos.quantity + quantity_delta)

    if new_qty == Decimal("0"):
        await db.delete(pos)
        audit.log_position_change(
            player_id=player.id,
            ticker=ticker,
            qty_before=qty_before,
            qty_after=Decimal("0"),
            avg_entry_price=pos.avg_entry_price,
        )
        return

    # Recalculate VWAC only when adding to an existing directional position
    if quantity_delta > 0 and pos.quantity > 0:
        pos.avg_entry_price = calculate_vwac(
            pos.quantity, pos.avg_entry_price, quantity_delta, price
        )
    elif quantity_delta < 0 and pos.quantity < 0:
        abs_existing = abs(pos.quantity)
        abs_delta = abs(quantity_delta)
        pos.avg_entry_price = calculate_vwac(abs_existing, pos.avg_entry_price, abs_delta, price)

    pos.quantity = new_qty
    audit.log_position_change(
        player_id=player.id,
        ticker=ticker,
        qty_before=qty_before,
        qty_after=new_qty,
        avg_entry_price=pos.avg_entry_price,
    )
