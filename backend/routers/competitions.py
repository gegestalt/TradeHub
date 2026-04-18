import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, get_db
from dependencies import get_adapter, get_current_player
from models.player import Player
from schemas.competition import (
    CompetitionCreate,
    CompetitionOut,
    JoinRequest,
    JoinResponse,
    LeaderboardEntry,
)
from services import competition as competition_service

router = APIRouter()


@router.post("", status_code=201)
async def create_competition(data: CompetitionCreate, db: AsyncSession = Depends(get_db)):
    competition, player, token = await competition_service.create_competition(db, data)
    return {
        "competition": CompetitionOut.model_validate(competition),
        "player_id": player.id,
        "token": token,
        "lobby_code": competition.lobby_code,
    }


@router.get("/{code}", response_model=CompetitionOut)
async def get_competition(code: str, db: AsyncSession = Depends(get_db)):
    competition = await competition_service.get_competition(db, code)
    return CompetitionOut.model_validate(competition)


@router.post("/{code}/join", response_model=JoinResponse, status_code=201)
async def join_competition(code: str, req: JoinRequest, db: AsyncSession = Depends(get_db)):
    player, token = await competition_service.join_competition(db, code, req)
    return JoinResponse(
        player_id=player.id,
        token=token,
        display_name=player.display_name,
        cash_balance=player.cash_balance,
    )


@router.post("/{code}/start", response_model=CompetitionOut)
async def start_competition(
    code: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    competition = await competition_service.start_competition(db, code, current_player)
    return CompetitionOut.model_validate(competition)


@router.get("/{code}/leaderboard", response_model=list[LeaderboardEntry])
async def get_leaderboard(code: str, db: AsyncSession = Depends(get_db)):
    competition = await competition_service.get_competition(db, code)
    adapter = get_adapter(competition.data_source)
    return await competition_service.get_leaderboard(db, code, adapter)


@router.get("/{code}/leaderboard/stream")
async def leaderboard_stream(code: str):
    async def event_generator():
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    competition = await competition_service.get_competition(db, code)
                    adapter = get_adapter(competition.data_source)
                    entries = await competition_service.get_leaderboard(db, code, adapter)
                    payload = json.dumps([e.model_dump() for e in entries], default=str)
                    yield f"data: {payload}\n\n"
            except Exception:
                yield "data: {}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
