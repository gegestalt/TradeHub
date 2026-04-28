"""Lobby service — create and fetch lobbies by UUID."""

import random
import secrets
import string

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.competition import Competition
from models.player import Player
from models.user import User
from schemas.lobby import LobbyCreate
from services.ledger import record_starting_balance


def _generate_lobby_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


async def create_lobby(
    db: AsyncSession, data: LobbyCreate, user: User
) -> tuple[Competition, str]:
    """Create a lobby. Returns (competition, creator_player_token)."""
    for _ in range(10):
        code = _generate_lobby_code()
        existing = await db.execute(
            select(Competition).where(Competition.lobby_code == code)
        )
        if not existing.scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=500, detail="Failed to generate unique lobby code")

    lobby = Competition(
        name=data.name,
        lobby_code=code,
        starting_balance=data.starting_balance,
        asset_universe=data.asset_universe,
        max_players=data.max_players,
        duration_minutes=data.duration_minutes,
        data_source=data.data_source,
        fee_pct=data.fee_pct,
        max_leverage=data.max_leverage,
        allow_shorts=data.allow_shorts,
    )
    db.add(lobby)
    await db.flush()

    token = secrets.token_urlsafe(32)
    creator = Player(
        competition_id=lobby.id,
        user_id=user.id,
        display_name=user.display_name,
        token=token,
        cash_balance=data.starting_balance,
        is_creator=True,
    )
    db.add(creator)
    await db.flush()
    await record_starting_balance(db, creator.id, lobby.id, data.starting_balance)

    return lobby, token


async def get_lobby(db: AsyncSession, lobby_id: str) -> Competition:
    """Fetch a lobby by UUID. Raises 404 if not found."""
    result = await db.execute(
        select(Competition).where(Competition.id == lobby_id)
    )
    lobby = result.scalar_one_or_none()
    if lobby is None:
        raise HTTPException(status_code=404, detail=f"Lobby '{lobby_id}' not found")
    return lobby
