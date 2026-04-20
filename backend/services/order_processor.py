"""Background task that periodically scans pending conditional orders and fills them.

Handles: limit, stop_loss, take_profit, stop_limit orders.
GTC orders stay pending until filled, cancelled, or the competition ends.
OCO: when one leg fills, the other is automatically cancelled.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_adapters.base import DataAdapter
from database import AsyncSessionLocal
from dependencies import get_adapter
from metrics import time_adapter_call
from models.competition import Competition
from models.enums import CompetitionState, OrderStatus, OrderType
from models.order import Order
from models.player import Player
from services.order_engine import _execute_fill, fill_price_for, should_fill

logger = logging.getLogger(__name__)

_MARKET_TYPES = {OrderType.market}
_CONDITIONAL_TYPES = {
    OrderType.limit,
    OrderType.stop_loss,
    OrderType.take_profit,
    OrderType.stop_limit,
}


async def process_pending_orders(db: AsyncSession) -> int:
    """Scan and fill all eligible pending conditional orders. Returns fill count."""
    # Load pending conditional orders with their players and competitions in one query
    result = await db.execute(
        select(Order, Player, Competition)
        .join(Player, Order.player_id == Player.id)
        .join(Competition, Player.competition_id == Competition.id)
        .where(
            Order.status == OrderStatus.pending,
            Order.order_type.in_(list(_CONDITIONAL_TYPES)),
            Competition.state == CompetitionState.active,
        )
    )
    rows = result.all()

    if not rows:
        return 0

    # Group by competition to fetch prices once per competition
    by_competition: dict[str, tuple[Competition, DataAdapter, list[tuple[Order, Player]]]] = {}
    for order, player, comp in rows:
        if comp.id not in by_competition:
            adapter = get_adapter(comp)
            by_competition[comp.id] = (comp, adapter, [])
        by_competition[comp.id][2].append((order, player))

    filled_count = 0
    for comp, adapter, order_players in by_competition.values():
        # Cache prices per ticker within this competition batch
        price_cache: dict[str, object] = {}

        for order, player in order_players:
            if order.status != OrderStatus.pending:
                # May have been cancelled by a sibling OCO fill earlier in this batch
                continue

            ticker = order.ticker
            if ticker not in price_cache:
                try:
                    with time_adapter_call():
                        price_cache[ticker] = adapter.get_price(ticker)
                except Exception:
                    logger.warning("processor: could not fetch price for %s", ticker)
                    continue

            current_price = price_cache[ticker]

            if not should_fill(order, current_price):
                continue

            exec_price = fill_price_for(order, current_price)
            try:
                await _execute_fill(db, player, comp, order, exec_price)
                filled_count += 1
            except Exception as exc:
                # Fill can fail legitimately (insufficient balance/position); log and move on
                logger.debug("processor: order %s not filled: %s", order.id, exc)

    return filled_count


async def run_order_processor(interval_seconds: int) -> None:
    """Long-running background coroutine. Launched from app lifespan."""
    logger.info("Order processor started (interval=%ds)", interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with AsyncSessionLocal() as db:
                filled = await process_pending_orders(db)
                if filled:
                    await db.commit()
                    logger.debug("Order processor: filled %d orders", filled)
        except Exception:
            logger.exception("Order processor error")
