"""Competition benchmark: average portfolio performance trajectory.

Groups PortfolioSnapshot records by snapshot timestamp and computes the mean
total_value across all players at each point in time. Returns the result as a
percentage-change series from starting_balance — the "competition average" line
that players compare their own P&L chart against.

Spectators never appear here because the snapshot task excludes them at write time.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.portfolio_snapshot import PortfolioSnapshot
from services.financials import quantize


async def get_competition_benchmark(
    db: AsyncSession,
    competition_id: str,
    starting_balance: Decimal,
) -> list[dict]:
    """Return average portfolio performance per snapshot cycle.

    Each entry covers one snapshot cycle. Fields:
      timestamp       — when the snapshot was taken (ISO-8601)
      avg_total_value — mean total portfolio value across all players
      avg_pnl         — avg_total_value - starting_balance
      avg_pnl_pct     — avg_pnl / starting_balance × 100 (4 decimal places)
      player_count    — how many players contributed to this data point
    """
    result = await db.execute(
        select(
            PortfolioSnapshot.recorded_at,
            func.avg(PortfolioSnapshot.total_value).label("avg_total"),
            func.count(PortfolioSnapshot.player_id).label("n"),
        )
        .where(PortfolioSnapshot.competition_id == competition_id)
        .group_by(PortfolioSnapshot.recorded_at)
        .order_by(PortfolioSnapshot.recorded_at.asc())
    )

    series = []
    for recorded_at, avg_total_raw, player_count in result.all():
        avg_total = quantize(Decimal(str(avg_total_raw)))
        pnl = quantize(avg_total - starting_balance)
        pnl_pct = (pnl / starting_balance * 100) if starting_balance else Decimal("0")
        series.append({
            "timestamp": recorded_at.isoformat(),
            "avg_total_value": str(avg_total),
            "avg_pnl": str(pnl),
            "avg_pnl_pct": f"{float(pnl_pct):.4f}",
            "player_count": player_count,
        })

    return series
