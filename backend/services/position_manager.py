from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import audit
from models.player import Player
from models.position import Position
from services.financials import calculate_vwac, quantize


async def get_position(db: AsyncSession, player_id: str, ticker: str) -> Position | None:
    result = await db.execute(
        select(Position).where(Position.player_id == player_id, Position.ticker == ticker)
    )
    return result.scalar_one_or_none()


async def apply_position_delta(
    db: AsyncSession,
    player: Player,
    ticker: str,
    quantity_delta: Decimal,
    price: Decimal,
) -> None:
    pos = await get_position(db, player.id, ticker)

    if pos is None:
        qty = quantize(quantity_delta)
        try:
            # Use a savepoint so a concurrent-insert IntegrityError only rolls back
            # this one statement — not the outer transaction (balance deduction, etc.).
            async with db.begin_nested():
                db.add(Position(
                    player_id=player.id, ticker=ticker,
                    quantity=qty, avg_entry_price=quantize(price),
                ))
        except IntegrityError:
            # Another concurrent fill already created this (player, ticker) row.
            # Re-read it and fall through to the update path below.
            pos = await get_position(db, player.id, ticker)
        else:
            await audit.log_position_change(
                db=db,
                player_id=player.id, ticker=ticker,
                qty_before=Decimal("0"), qty_after=qty, avg_entry_price=quantize(price),
            )
            return

    qty_before = pos.quantity
    new_qty = quantize(pos.quantity + quantity_delta)

    if new_qty == Decimal("0"):
        await db.delete(pos)
        await audit.log_position_change(
            db=db,
            player_id=player.id, ticker=ticker,
            qty_before=qty_before, qty_after=Decimal("0"), avg_entry_price=pos.avg_entry_price,
        )
        return

    if quantity_delta > 0 and pos.quantity > 0:
        pos.avg_entry_price = calculate_vwac(
            pos.quantity, pos.avg_entry_price, quantity_delta, price
        )
    elif quantity_delta < 0 and pos.quantity < 0:
        pos.avg_entry_price = calculate_vwac(
            abs(pos.quantity), pos.avg_entry_price, abs(quantity_delta), price
        )

    pos.quantity = new_qty
    await audit.log_position_change(
        db=db,
        player_id=player.id, ticker=ticker,
        qty_before=qty_before, qty_after=new_qty, avg_entry_price=pos.avg_entry_price,
    )
