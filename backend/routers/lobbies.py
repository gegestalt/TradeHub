"""Lobby endpoints.

POST /lobbies        — create a lobby, returns UUID + join code + attributes
GET  /lobbies/{id}   — fetch lobby by UUID, returns attributes
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas.lobby import LobbyCreate, LobbyOut
from services.lobby import create_lobby, get_lobby

router = APIRouter(prefix="/lobbies", tags=["lobbies"])


@router.post("", status_code=201)
async def create(data: LobbyCreate, db: AsyncSession = Depends(get_db)):
    """Create a lobby. Returns the UUID and all lobby attributes."""
    lobby, token = await create_lobby(db, data)
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
