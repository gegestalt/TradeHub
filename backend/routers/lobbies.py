"""Lobby endpoints.

POST /lobbies        — create a lobby (requires registered user token)
GET  /lobbies/{id}   — fetch lobby by UUID
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from schemas.lobby import LobbyCreate, LobbyOut
from services.lobby import create_lobby, get_lobby

router = APIRouter(prefix="/lobbies", tags=["lobbies"])


@router.post("", status_code=201)
async def create(
    data: LobbyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a lobby. The authenticated user becomes the creator."""
    lobby, token = await create_lobby(db, data, current_user)
    return {
        "id": lobby.id,
        "lobby_code": lobby.lobby_code,
        "token": token,
        "lobby": LobbyOut.model_validate(lobby),
    }


@router.get("/{lobby_id}", response_model=LobbyOut)
async def get(lobby_id: str, db: AsyncSession = Depends(get_db)):
    """Fetch lobby attributes by UUID."""
    lobby = await get_lobby(db, lobby_id)
    return LobbyOut.model_validate(lobby)
