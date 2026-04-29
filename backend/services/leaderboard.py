import math
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from data_adapters.base import DataAdapter
from models.competition import Competition
from models.enums import OrderStatus, ScoringMethod
from models.order import Order
from models.player import Player
from models.portfolio_snapshot import PortfolioSnapshot
from models.position import Position
from schemas.competition import LeaderboardEntry, PositionSummary

_TWO_DP = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    """Round to 2 decimal places for all display monetary values."""
    return value.quantize(_TWO_DP, rounding=ROUND_HALF_UP)


async def get_leaderboard(
    db: AsyncSession, code: str, adapter: DataAdapter
) -> list[LeaderboardEntry]:
    from fastapi import HTTPException
    from sqlalchemy import select as sa_select

    from models.competition import Competition as Comp

    result = await db.execute(sa_select(Comp).where(Comp.lobby_code == code))
    competition = result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail=f"Competition '{code}' not found")

    players_result = await db.execute(
        select(Player).where(
            Player.competition_id == competition.id,
            Player.spectator.is_(False),
        )
    )
    players = players_result.scalars().all()
    if not players:
        return []

    player_ids = [p.id for p in players]

    # Batch 1: all positions for all players in this competition
    pos_result = await db.execute(
        select(Position).where(Position.player_id.in_(player_ids))
    )
    all_positions = pos_result.scalars().all()
    positions_by_player: dict[str, list[Position]] = defaultdict(list)
    for pos in all_positions:
        positions_by_player[pos.player_id].append(pos)

    # Batch 2: filled order counts per player (single GROUP BY query)
    count_result = await db.execute(
        select(Order.player_id, func.count().label("n"))
        .where(Order.player_id.in_(player_ids), Order.status == OrderStatus.filled)
        .group_by(Order.player_id)
    )
    orders_filled_map: dict[str, int] = {row.player_id: row.n for row in count_result.all()}

    # Batch 3: all portfolio snapshots for Sharpe scoring (single query for competition)
    if competition.scoring_method == ScoringMethod.sharpe_ratio:
        snap_result = await db.execute(
            select(PortfolioSnapshot)
            .where(
                PortfolioSnapshot.competition_id == competition.id,
                PortfolioSnapshot.player_id.in_(player_ids),
            )
            .order_by(PortfolioSnapshot.player_id, PortfolioSnapshot.recorded_at.asc())
        )
        all_snapshots = snap_result.scalars().all()
        snapshots_by_player: dict[str, list[PortfolioSnapshot]] = defaultdict(list)
        for snap in all_snapshots:
            snapshots_by_player[snap.player_id].append(snap)
    else:
        snapshots_by_player = defaultdict(list)

    entries = []
    for player in players:
        position_summaries, positions_value, unrealized_pnl = _build_position_summaries(
            positions_by_player[player.id], adapter
        )
        orders_filled = orders_filled_map.get(player.id, 0)
        cash = _q(player.cash_balance)
        total_value = _q(cash + positions_value)
        pnl = _q(total_value - competition.starting_balance)
        pnl_pct = _q(
            pnl / competition.starting_balance * 100
            if competition.starting_balance
            else Decimal("0")
        )
        realized = _q(player.realized_pnl)
        player_snaps = snapshots_by_player[player.id]
        score = _compute_score(player_snaps, competition, total_value)
        max_dd = _compute_max_drawdown(player_snaps)

        entries.append(
            LeaderboardEntry(
                rank=0,
                player_id=player.id,
                display_name=player.display_name,
                cash_balance=cash,
                positions_value=positions_value,
                total_value=total_value,
                pnl=pnl,
                pnl_pct=pnl_pct,
                realized_pnl=realized,
                unrealized_pnl=unrealized_pnl,
                orders_filled=orders_filled,
                positions=position_summaries,
                score=score,
                max_drawdown_pct=max_dd,
            )
        )

    entries.sort(key=lambda e: e.score, reverse=True)
    for i, entry in enumerate(entries):
        entry.rank = i + 1

    return entries


def _compute_max_drawdown(snapshots: list[PortfolioSnapshot]) -> Decimal:
    """Return the max peak-to-trough drawdown as a percentage (0–100).

    Max drawdown = max((peak - trough) / peak) over the snapshot history.
    A value of 15.00 means the portfolio fell 15 % from its peak at some point.
    """
    if len(snapshots) < 2:
        return Decimal("0")

    values = [float(s.total_value) for s in snapshots]
    peak = values[0]
    max_dd = 0.0

    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

    return Decimal(str(round(max_dd * 100, 4)))


def _compute_score(
    snapshots: list[PortfolioSnapshot],
    competition: Competition,
    current_total_value: Decimal,
) -> Decimal:
    if competition.scoring_method != ScoringMethod.sharpe_ratio:
        return current_total_value

    if len(snapshots) < 2:
        return current_total_value

    values = [float(s.total_value) for s in snapshots]
    returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]

    if len(returns) < 2:
        return current_total_value

    n = len(returns)
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0

    if std_r == 0:
        return current_total_value

    sharpe = (mean_r / std_r) * math.sqrt(8766)
    return Decimal(str(round(sharpe, 8)))


# Keep the async version for callers that need it stand-alone (e.g. competition end scoring)
async def compute_score(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    current_total_value: Decimal,
) -> Decimal:
    if competition.scoring_method != ScoringMethod.sharpe_ratio:
        return current_total_value

    snapshots_result = await db.execute(
        select(PortfolioSnapshot)
        .where(
            PortfolioSnapshot.player_id == player.id,
            PortfolioSnapshot.competition_id == competition.id,
        )
        .order_by(PortfolioSnapshot.recorded_at.asc())
    )
    snapshots = snapshots_result.scalars().all()
    return _compute_score(snapshots, competition, current_total_value)


def _build_position_summaries(
    raw_positions: list[Position],
    adapter: DataAdapter,
) -> tuple[list[PositionSummary], Decimal, Decimal]:
    summaries: list[PositionSummary] = []
    positions_value = Decimal("0")
    unrealized_pnl = Decimal("0")

    for pos in raw_positions:
        try:
            current_price = _q(adapter.get_price(pos.ticker))
        except Exception:
            current_price = _q(pos.avg_entry_price)

        market_value = _q(current_price * pos.quantity)
        pos_unrealized = _q((current_price - pos.avg_entry_price) * pos.quantity)
        positions_value += market_value
        unrealized_pnl += pos_unrealized

        summaries.append(
            PositionSummary(
                ticker=pos.ticker,
                quantity=pos.quantity,
                avg_entry_price=_q(pos.avg_entry_price),
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=pos_unrealized,
            )
        )

    return summaries, positions_value, unrealized_pnl


async def calc_positions_value(
    db: AsyncSession, player: Player, adapter: DataAdapter
) -> Decimal:
    result = await db.execute(select(Position).where(Position.player_id == player.id))
    positions = result.scalars().all()

    total = Decimal("0")
    for pos in positions:
        try:
            price = adapter.get_price(pos.ticker)
            total += price * pos.quantity
        except Exception:
            total += pos.avg_entry_price * pos.quantity
    return total


async def _count_filled_orders(db: AsyncSession, player_id: str) -> int:
    result = await db.execute(
        select(func.count()).where(
            Order.player_id == player_id,
            Order.status == OrderStatus.filled,
        )
    )
    return result.scalar_one()
