from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_adapters.base import DataAdapter
from models.competition import Competition
from models.enums import CompetitionState, OrderSide, OrderStatus, OrderType
from models.order import Order
from models.player import Player
from models.position import Position
from schemas.order import OrderCreate


async def place_order(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    data: OrderCreate,
    adapter: DataAdapter,
) -> Order:
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
        quantity=data.quantity,
        limit_price=data.limit_price,
        stop_price=data.stop_price,
        status=OrderStatus.pending,
    )
    db.add(order)
    await db.flush()

    if data.order_type == OrderType.market:
        await _fill_market_order(db, player, competition, order, price)

    return order


async def _fill_market_order(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
) -> None:
    fee = price * order.quantity * competition.fee_pct
    gross_value = price * order.quantity

    if order.side == OrderSide.buy:
        total_cost = gross_value + fee
        if player.cash_balance < total_cost:
            order.status = OrderStatus.cancelled
            raise HTTPException(status_code=400, detail="Insufficient cash balance")

        player.cash_balance -= total_cost
        await _update_position(db, player.id, order.ticker, order.quantity, price)

    else:
        pos = await _get_position(db, player.id, order.ticker)

        if not competition.allow_shorts:
            if not pos or pos.quantity < order.quantity:
                order.status = OrderStatus.cancelled
                raise HTTPException(status_code=400, detail="Insufficient position quantity to sell")

        proceeds = gross_value - fee
        player.cash_balance += proceeds
        await _update_position(db, player.id, order.ticker, -order.quantity, price)

    order.fill_price = price
    order.fill_at = datetime.utcnow()
    order.fee_paid = fee
    order.status = OrderStatus.filled


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
        db.add(
            Position(
                player_id=player_id,
                ticker=ticker,
                quantity=quantity_delta,
                avg_entry_price=price,
            )
        )
        return

    new_qty = pos.quantity + quantity_delta

    if new_qty == Decimal("0"):
        await db.delete(pos)
        return

    # VWAC when adding to an existing directional position
    if quantity_delta > 0 and pos.quantity > 0:
        total_cost = pos.avg_entry_price * pos.quantity + price * quantity_delta
        pos.avg_entry_price = total_cost / new_qty
    elif quantity_delta < 0 and pos.quantity < 0:
        abs_existing = abs(pos.quantity)
        abs_delta = abs(quantity_delta)
        total_cost = pos.avg_entry_price * abs_existing + price * abs_delta
        pos.avg_entry_price = total_cost / (abs_existing + abs_delta)

    pos.quantity = new_qty
