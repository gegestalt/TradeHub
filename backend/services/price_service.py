"""Snapshot-backed price resolution.

The project spec states: "Order fills reference these snapshots, not live
prices, to ensure consistency and support replay."

This module is the single point for reading PriceSnapshot records from the
database — used by replay, analytics, and the history endpoint.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.price_snapshot import PriceSnapshot


async def get_price_at(
    db: AsyncSession,
    competition_id: str,
    ticker: str,
    at: datetime,
) -> Decimal | None:
    """Return the most recent snapshotted price at or before `at`.

    Returns None if no snapshot exists for the ticker/competition before `at`.
    """
    result = await db.execute(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.competition_id == competition_id,
            PriceSnapshot.ticker == ticker.upper(),
            PriceSnapshot.recorded_at <= at,
        )
        .order_by(PriceSnapshot.recorded_at.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    return snapshot.price if snapshot else None


async def get_latest_snapshot_price(
    db: AsyncSession,
    competition_id: str,
    ticker: str,
) -> Decimal | None:
    """Return the most recent snapshotted price for a ticker, regardless of time."""
    result = await db.execute(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.competition_id == competition_id,
            PriceSnapshot.ticker == ticker.upper(),
        )
        .order_by(PriceSnapshot.recorded_at.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    return snapshot.price if snapshot else None


async def get_price_timeline(
    db: AsyncSession,
    competition_id: str,
    ticker: str,
) -> list[tuple[datetime, Decimal]]:
    """Return all (timestamp, price) pairs for a ticker in chronological order.

    Used for replay and post-mortem analysis.
    """
    result = await db.execute(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.competition_id == competition_id,
            PriceSnapshot.ticker == ticker.upper(),
        )
        .order_by(PriceSnapshot.recorded_at.asc())
    )
    return [(s.recorded_at, s.price) for s in result.scalars().all()]
