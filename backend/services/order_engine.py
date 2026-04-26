import time
import uuid
from datetime import datetime
from decimal import Decimal

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
from schemas.order import OCOCreate, OrderCreate
from services.financials import calculate_buy_cost, calculate_fee, calculate_sell_proceeds, quantize
from services.position_manager import apply_position_delta, get_position

_q = quantize


def calculate_vwac(
    existing_qty: Decimal,
    existing_price: Decimal,
    new_qty: Decimal,
    new_price: Decimal,
) -> Decimal:
    from services.financials import calculate_vwac as _vwac
    return _vwac(existing_qty, existing_price, new_qty, new_price)


def should_fill(order: Order, current_price: Decimal) -> bool:
    t = order.order_type
    s = order.side

    if t == OrderType.limit:
        if s == OrderSide.buy:
            return order.limit_price is not None and current_price <= order.limit_price
        return order.limit_price is not None and current_price >= order.limit_price

    if t == OrderType.stop_loss:
        if s == OrderSide.sell:
            return order.stop_price is not None and current_price <= order.stop_price
        return order.stop_price is not None and current_price >= order.stop_price

    if t == OrderType.take_profit:
        if s == OrderSide.sell:
            return order.take_profit_price is not None and current_price >= order.take_profit_price
        return order.take_profit_price is not None and current_price <= order.take_profit_price

    if t == OrderType.stop_limit:
        if s == OrderSide.sell:
            triggered = order.stop_price is not None and current_price <= order.stop_price
            at_limit = order.limit_price is not None and current_price >= order.limit_price
            return triggered and at_limit
        triggered = order.stop_price is not None and current_price >= order.stop_price
        at_limit = order.limit_price is not None and current_price <= order.limit_price
        return triggered and at_limit

    return False


def fill_price_for(order: Order, current_price: Decimal) -> Decimal:
    t = order.order_type
    if t == OrderType.limit:
        return order.limit_price  # type: ignore[return-value]
    if t == OrderType.stop_loss:
        return current_price
    if t == OrderType.take_profit:
        return order.take_profit_price  # type: ignore[return-value]
    if t == OrderType.stop_limit:
        return order.limit_price  # type: ignore[return-value]
    return current_price


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
        _validate_order_context(ticker, competition)

        current_price = adapter.get_price(ticker)
        _validate_leverage(data, player, competition, current_price)

        order = _build_order(player.id, data, ticker)
        with db.no_autoflush:
            db.add(order)
        await db.flush()

        if data.order_type == OrderType.market:
            await execute_fill(db, player, competition, order, current_price)

        elif should_fill(order, current_price):
            await execute_fill(db, player, competition, order, fill_price_for(order, current_price))
        elif data.time_in_force in (TimeInForce.ioc, TimeInForce.fok):
            order.status = OrderStatus.expired

        filled = order.status == OrderStatus.filled
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
    ticker = data.ticker.upper()
    _validate_order_context(ticker, competition)

    pair_id = str(uuid.uuid4())
    sl_order = Order(
        player_id=player.id, ticker=ticker, order_type=OrderType.stop_loss,
        side=data.side, quantity=quantize(data.quantity),
        stop_price=data.stop_price, time_in_force=TimeInForce.gtc,
        oco_pair_id=pair_id, status=OrderStatus.pending,
    )
    tp_order = Order(
        player_id=player.id, ticker=ticker, order_type=OrderType.take_profit,
        side=data.side, quantity=quantize(data.quantity),
        take_profit_price=data.take_profit_price, time_in_force=TimeInForce.gtc,
        oco_pair_id=pair_id, status=OrderStatus.pending,
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
    await _cancel_oco_sibling(db, order)
    return order


async def execute_fill(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
) -> None:
    async with get_player_lock(player.id):
        # Refresh the player from DB inside the lock so we always act on the
        # latest committed balance — not the stale value loaded at request start.
        # This prevents overspending when multiple fills for the same player queue up.
        await db.refresh(player)

        with db.no_autoflush:
            balance_before = player.cash_balance
            fee = calculate_fee(price, order.quantity, competition.fee_pct)

            if order.side == OrderSide.buy:
                await _execute_buy(db, player, competition, order, price, fee)
            else:
                await _execute_sell(db, player, competition, order, price, fee)

            order.fill_price = price
            order.fill_at = datetime.utcnow()
            order.fee_paid = fee
            order.status = OrderStatus.filled

            audit.log_order_fill(
                player_id=player.id, competition_id=competition.id, order_id=order.id,
                ticker=order.ticker, side=order.side, quantity=order.quantity,
                fill_price=price, fee=fee,
                balance_before=balance_before, balance_after=player.cash_balance,
            )
            await _cancel_oco_sibling(db, order)


async def _execute_buy(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
    fee: Decimal,
) -> None:
    cost = calculate_buy_cost(price, order.quantity, competition.fee_pct)
    if player.cash_balance < cost:
        order.status = OrderStatus.cancelled
        audit.log_order_rejected(
            player_id=player.id, order_id=order.id, ticker=order.ticker,
            reason=f"Insufficient balance: have {player.cash_balance}, need {cost}",
        )
        raise HTTPException(status_code=400, detail="Insufficient cash balance")

    player.cash_balance = quantize(player.cash_balance - cost)

    short_pos = await get_position(db, player.id, order.ticker)
    if short_pos and short_pos.quantity < 0:
        qty_covered = min(order.quantity, abs(short_pos.quantity))
        cover_fee = calculate_fee(price, qty_covered, competition.fee_pct)
        player.realized_pnl = quantize(
            player.realized_pnl + (short_pos.avg_entry_price - price) * qty_covered - cover_fee
        )

    await apply_position_delta(db, player, order.ticker, order.quantity, price)


async def _execute_sell(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
    fee: Decimal,
) -> None:
    pos = await get_position(db, player.id, order.ticker)
    available = pos.quantity if pos else Decimal("0")

    if not competition.allow_shorts and available < order.quantity:
        order.status = OrderStatus.cancelled
        audit.log_order_rejected(
            player_id=player.id, order_id=order.id, ticker=order.ticker,
            reason=f"Insufficient position: have {available}, need {order.quantity}",
        )
        raise HTTPException(status_code=400, detail="Insufficient position quantity to sell")

    proceeds = calculate_sell_proceeds(price, order.quantity, competition.fee_pct)
    player.cash_balance = quantize(player.cash_balance + proceeds)

    if pos and pos.quantity > 0:
        qty_closed = min(order.quantity, pos.quantity)
        cost_basis = pos.avg_entry_price * qty_closed
        gross_revenue = price * qty_closed
        close_fee = calculate_fee(price, qty_closed, competition.fee_pct)
        player.realized_pnl = quantize(
            player.realized_pnl + gross_revenue - cost_basis - close_fee
        )

    await apply_position_delta(db, player, order.ticker, -order.quantity, price)


async def _cancel_oco_sibling(db: AsyncSession, order: Order) -> None:
    if not order.oco_pair_id:
        return
    result = await db.execute(
        select(Order).where(
            Order.oco_pair_id == order.oco_pair_id,
            Order.id != order.id,
            Order.status == OrderStatus.pending,
        )
    )
    sibling = result.scalar_one_or_none()
    if sibling:
        sibling.status = OrderStatus.cancelled


def _validate_order_context(ticker: str, competition: Competition) -> None:
    if ticker not in [t.upper() for t in competition.asset_universe]:
        raise HTTPException(
            status_code=400,
            detail=f"Ticker '{ticker}' is not in the competition's asset universe",
        )
    if competition.state != CompetitionState.active:
        raise HTTPException(status_code=400, detail="Competition is not active")


def _validate_leverage(
    data: OrderCreate,
    player: Player,
    competition: Competition,
    current_price: Decimal,
) -> None:
    if data.side != OrderSide.buy or competition.max_leverage <= Decimal("0"):
        return
    notional = quantize(current_price * quantize(data.quantity))
    max_notional = quantize(player.cash_balance * competition.max_leverage)
    if notional > max_notional:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order notional {notional} exceeds max leverage "
                f"({competition.max_leverage}x) on balance {player.cash_balance}"
            ),
        )


def _build_order(player_id: str, data: OrderCreate, ticker: str) -> Order:
    # Set all Python-side defaults explicitly so fields are populated immediately
    # on the in-memory object, before the session flushes the INSERT.
    # SQLAlchemy's column `default=` expressions only run at flush time; if code
    # reads order.id / order.fee_paid / order.created_at before the first flush
    # (e.g. to return the order, or in the concurrent-session tests) those fields
    # would be None otherwise.
    return Order(
        id=str(uuid.uuid4()),
        player_id=player_id,
        ticker=ticker,
        order_type=data.order_type,
        side=data.side,
        quantity=quantize(data.quantity),
        limit_price=data.limit_price,
        stop_price=data.stop_price,
        take_profit_price=data.take_profit_price,
        time_in_force=data.time_in_force,
        status=OrderStatus.pending,
        fee_paid=Decimal("0"),
        created_at=datetime.utcnow(),
    )


async def _load_player(db: AsyncSession, player_id: str) -> Player:
    result = await db.execute(select(Player).where(Player.id == player_id))
    return result.scalar_one()
