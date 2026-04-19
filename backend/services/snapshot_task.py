"""Background task: snapshot prices and portfolio values for active competitions.

Runs every PRICE_SNAPSHOT_INTERVAL_SECONDS (default 60s). For each active competition:
  1. Records current price for every ticker → PriceSnapshot
  2. Computes total portfolio value for each player → PortfolioSnapshot
  3. Auto-ends competitions whose end_at has passed
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from dependencies import get_adapter
from models.competition import Competition
from models.enums import CompetitionState, PriceSource
from models.player import Player
from models.portfolio_snapshot import PortfolioSnapshot
from models.position import Position
from models.price_snapshot import PriceSnapshot

logger = logging.getLogger(__name__)


async def _snapshot_competition(db: AsyncSession, competition: Competition) -> None:
    adapter = get_adapter(competition.data_source)
    now = datetime.utcnow()

    # Auto-end if end_at has passed
    if competition.end_at and now >= competition.end_at:
        competition.state = CompetitionState.ended
        logger.info("snapshot_task: auto-ended competition %s", competition.lobby_code)
        return

    # Fetch and record current prices for all tickers
    prices: dict[str, Decimal] = {}
    for ticker in competition.asset_universe:
        t = ticker.upper()
        try:
            price = adapter.get_price(t)
            prices[t] = price
            db.add(
                PriceSnapshot(
                    competition_id=competition.id,
                    ticker=t,
                    price=price,
                    recorded_at=now,
                    source=PriceSource(competition.data_source),
                )
            )
        except Exception:
            logger.debug("snapshot_task: could not fetch price for %s", t)

    # Snapshot portfolio value for each active (non-spectator) player
    players_result = await db.execute(
        select(Player).where(
            Player.competition_id == competition.id,
            Player.spectator.is_(False),
        )
    )
    players = players_result.scalars().all()

    for player in players:
        positions_result = await db.execute(
            select(Position).where(Position.player_id == player.id)
        )
        positions = positions_result.scalars().all()

        positions_value = Decimal("0")
        for pos in positions:
            price = prices.get(pos.ticker, pos.avg_entry_price)
            positions_value += price * pos.quantity

        total_value = player.cash_balance + positions_value
        pnl = total_value - competition.starting_balance

        db.add(
            PortfolioSnapshot(
                player_id=player.id,
                competition_id=competition.id,
                total_value=total_value,
                cash_balance=player.cash_balance,
                positions_value=positions_value,
                pnl=pnl,
                recorded_at=now,
            )
        )


async def run_snapshot_task(interval_seconds: int) -> None:
    """Long-running background coroutine. Launched from app lifespan."""
    logger.info("Snapshot task started (interval=%ds)", interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Competition).where(Competition.state == CompetitionState.active)
                )
                active = result.scalars().all()
                for competition in active:
                    await _snapshot_competition(db, competition)
                await db.commit()
                if active:
                    logger.debug("snapshot_task: snapshotted %d competitions", len(active))
        except Exception:
            logger.exception("snapshot_task error")
