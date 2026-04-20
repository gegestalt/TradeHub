import math
from decimal import Decimal

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

    entries = []
    for player in players:
        position_summaries, positions_value, unrealized_pnl = await _build_position_summaries(
            db, player, adapter
        )
        orders_filled = await _count_filled_orders(db, player.id)
        total_value = player.cash_balance + positions_value
        pnl = total_value - competition.starting_balance
        pnl_pct = (
            pnl / competition.starting_balance * 100
            if competition.starting_balance
            else Decimal("0")
        )
        score = await compute_score(db, player, competition, total_value)

        entries.append(
            LeaderboardEntry(
                rank=0,
                player_id=player.id,
                display_name=player.display_name,
                cash_balance=player.cash_balance,
                positions_value=positions_value,
                total_value=total_value,
                pnl=pnl,
                pnl_pct=pnl_pct,
                realized_pnl=player.realized_pnl,
                unrealized_pnl=unrealized_pnl,
                orders_filled=orders_filled,
                positions=position_summaries,
                score=score,
            )
        )

    entries.sort(key=lambda e: e.score, reverse=True)
    for i, entry in enumerate(entries):
        entry.rank = i + 1

    return entries


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


async def _build_position_summaries(
    db: AsyncSession,
    player: Player,
    adapter: DataAdapter,
) -> tuple[list[PositionSummary], Decimal, Decimal]:
    pos_result = await db.execute(select(Position).where(Position.player_id == player.id))
    raw_positions = pos_result.scalars().all()

    summaries: list[PositionSummary] = []
    positions_value = Decimal("0")
    unrealized_pnl = Decimal("0")

    for pos in raw_positions:
        try:
            current_price = adapter.get_price(pos.ticker)
        except Exception:
            current_price = pos.avg_entry_price

        market_value = current_price * pos.quantity
        pos_unrealized = (current_price - pos.avg_entry_price) * pos.quantity
        positions_value += market_value
        unrealized_pnl += pos_unrealized

        summaries.append(
            PositionSummary(
                ticker=pos.ticker,
                quantity=pos.quantity,
                avg_entry_price=pos.avg_entry_price,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=pos_unrealized,
            )
        )

    return summaries, positions_value, unrealized_pnl


async def _count_filled_orders(db: AsyncSession, player_id: str) -> int:
    result = await db.execute(
        select(func.count()).where(
            Order.player_id == player_id,
            Order.status == OrderStatus.filled,
        )
    )
    return result.scalar_one()
