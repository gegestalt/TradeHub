import random
import secrets
import string
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_adapters.base import DataAdapter
from data_adapters.mock import MockDataAdapter
from models.competition import Competition
from models.enums import CompetitionState, DataSource
from models.player import Player
from models.user import User
from schemas.competition import CompetitionCreate, LeaderboardEntry
from services.ledger import record_starting_balance


def _generate_lobby_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=6))


async def create_competition(
    db: AsyncSession, data: CompetitionCreate, user: User
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
        max_players=data.max_players,
        duration_minutes=data.duration_minutes,
    )
    db.add(competition)
    await db.flush()

    token = secrets.token_urlsafe(32)
    player = Player(
        competition_id=competition.id,
        user_id=user.id,
        display_name=user.display_name,
        token=token,
        cash_balance=data.starting_balance,
        is_creator=True,
    )
    db.add(player)
    await db.flush()
    await record_starting_balance(db, player.id, competition.id, data.starting_balance)

    return competition, player, token


async def join_competition(
    db: AsyncSession, code: str, user: User, spectator: bool = False
) -> tuple[Player, str]:
    competition = await get_competition(db, code)

    if spectator:
        if competition.state == CompetitionState.ended:
            raise HTTPException(status_code=400, detail="Competition has ended")
    else:
        if competition.state != CompetitionState.lobby:
            raise HTTPException(status_code=400, detail="Competition has already started or ended")
        if competition.max_players:
            count_result = await db.execute(
                select(func.count()).where(
                    Player.competition_id == competition.id,
                    Player.spectator.is_(False),
                )
            )
            count = count_result.scalar_one()
            if count >= competition.max_players:
                raise HTTPException(
                    status_code=400,
                    detail=f"Competition is full ({competition.max_players} players max)",
                )

    # Prevent a registered user from joining the same competition twice
    existing = await db.execute(
        select(Player).where(
            Player.competition_id == competition.id,
            Player.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409, detail="You have already joined this competition"
        )

    token = secrets.token_urlsafe(32)
    balance = Decimal("0") if spectator else competition.starting_balance
    player = Player(
        competition_id=competition.id,
        user_id=user.id,
        display_name=user.display_name,
        token=token,
        cash_balance=balance,
        spectator=spectator,
    )
    db.add(player)
    await db.flush()
    if not spectator:
        await record_starting_balance(db, player.id, competition.id, balance)
    return player, token


async def start_competition(
    db: AsyncSession, code: str, player: Player
) -> Competition:
    competition = await get_competition(db, code)

    if competition.state != CompetitionState.lobby:
        raise HTTPException(status_code=400, detail="Competition is not in lobby state")
    if not player.is_creator:
        raise HTTPException(status_code=403, detail="Only the creator can start the competition")
    if player.competition_id != competition.id:
        raise HTTPException(status_code=403, detail="Player does not belong to this competition")

    competition.state = CompetitionState.active
    if not competition.start_at:
        competition.start_at = datetime.utcnow()
    if competition.duration_minutes and not competition.end_at:
        competition.end_at = competition.start_at + timedelta(minutes=competition.duration_minutes)

    return competition


async def end_competition(
    db: AsyncSession, code: str, player: Player
) -> Competition:
    competition = await get_competition(db, code)

    if competition.state != CompetitionState.active:
        raise HTTPException(status_code=400, detail="Competition is not active")
    if not player.is_creator:
        raise HTTPException(status_code=403, detail="Only the creator can end the competition")
    if player.competition_id != competition.id:
        raise HTTPException(status_code=403, detail="Player does not belong to this competition")

    competition.state = CompetitionState.ended
    competition.end_at = datetime.utcnow()

    if DataSource(competition.data_source) == DataSource.mock:
        MockDataAdapter.evict(competition.id)

    # Drain the order queue and evict the circuit breaker for this competition
    import asyncio
    from services.order_queue import drain as drain_queue
    from services.market_guardian import evict_guardian
    asyncio.create_task(drain_queue(competition.id))
    evict_guardian(competition.id)

    return competition


async def get_competition(db: AsyncSession, code: str) -> Competition:
    result = await db.execute(select(Competition).where(Competition.lobby_code == code))
    competition = result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail=f"Competition '{code}' not found")
    return competition


async def get_leaderboard(
    db: AsyncSession, code: str, adapter: DataAdapter
) -> list[LeaderboardEntry]:
    from services.leaderboard import get_leaderboard as _get_leaderboard
    return await _get_leaderboard(db, code, adapter)
