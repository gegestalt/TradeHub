import random
import secrets
import string
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_adapters.base import DataAdapter
from models.competition import Competition
from models.enums import CompetitionState
from models.player import Player
from models.position import Position
from schemas.competition import CompetitionCreate, JoinRequest, LeaderboardEntry


def _generate_lobby_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=6))


async def create_competition(
    db: AsyncSession, data: CompetitionCreate
) -> tuple[Competition, Player, str]:
    for _ in range(10):
        code = _generate_lobby_code()
        existing = await db.execute(select(Competition).where(Competition.lobby_code == code))
        if not existing.scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=500, detail="Failed to generate unique lobby code")

    competition = Competition(
        name=data.name,
        lobby_code=code,
        starting_balance=data.starting_balance,
        start_at=data.start_at,
        end_at=data.end_at,
        asset_universe=[t.upper() for t in data.asset_universe],
        data_source=data.data_source,
        scoring_method=data.scoring_method,
        fee_pct=data.fee_pct,
        max_leverage=data.max_leverage,
        allow_shorts=data.allow_shorts,
    )
    db.add(competition)
    await db.flush()

    token = secrets.token_urlsafe(32)
    player = Player(
        competition_id=competition.id,
        display_name=data.creator_name,
        token=token,
        cash_balance=data.starting_balance,
        is_creator=True,
    )
    db.add(player)
    await db.flush()

    return competition, player, token


async def join_competition(db: AsyncSession, code: str, req: JoinRequest) -> tuple[Player, str]:
    competition = await _get_competition_or_404(db, code)

    if competition.state != CompetitionState.lobby:
        raise HTTPException(status_code=400, detail="Competition has already started or ended")

    token = secrets.token_urlsafe(32)
    player = Player(
        competition_id=competition.id,
        display_name=req.display_name,
        token=token,
        cash_balance=competition.starting_balance,
    )
    db.add(player)
    await db.flush()
    return player, token


async def start_competition(db: AsyncSession, code: str, player: Player) -> Competition:
    competition = await _get_competition_or_404(db, code)

    if competition.state != CompetitionState.lobby:
        raise HTTPException(status_code=400, detail="Competition is not in lobby state")

    if not player.is_creator:
        raise HTTPException(status_code=403, detail="Only the creator can start the competition")

    if player.competition_id != competition.id:
        raise HTTPException(status_code=403, detail="Player does not belong to this competition")

    competition.state = CompetitionState.active
    if not competition.start_at:
        competition.start_at = datetime.utcnow()

    return competition


async def get_competition(db: AsyncSession, code: str) -> Competition:
    return await _get_competition_or_404(db, code)


async def get_leaderboard(
    db: AsyncSession, code: str, adapter: DataAdapter
) -> list[LeaderboardEntry]:
    competition = await _get_competition_or_404(db, code)

    result = await db.execute(
        select(Player).where(
            Player.competition_id == competition.id,
            Player.spectator.is_(False),
        )
    )
    players = result.scalars().all()

    entries = []
    for p in players:
        positions_value = await _calc_positions_value(db, p, adapter)
        total_value = p.cash_balance + positions_value
        pnl = total_value - competition.starting_balance
        if competition.starting_balance:
            pnl_pct = pnl / competition.starting_balance * 100
        else:
            pnl_pct = Decimal("0")

        entries.append(
            LeaderboardEntry(
                rank=0,
                player_id=p.id,
                display_name=p.display_name,
                cash_balance=p.cash_balance,
                positions_value=positions_value,
                total_value=total_value,
                pnl=pnl,
                pnl_pct=pnl_pct,
            )
        )

    entries.sort(key=lambda e: e.total_value, reverse=True)
    for i, entry in enumerate(entries):
        entry.rank = i + 1

    return entries


async def _calc_positions_value(db: AsyncSession, player: Player, adapter: DataAdapter) -> Decimal:
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


async def _get_competition_or_404(db: AsyncSession, code: str) -> Competition:
    result = await db.execute(select(Competition).where(Competition.lobby_code == code))
    competition = result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail=f"Competition '{code}' not found")
    return competition
