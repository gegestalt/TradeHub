"""Player analysis endpoints.

GET /competitions/{code}/players/{player_id}/analysis
    Returns full decision analysis, technique identification, and narrative
    commentary for a player's actions within a competition.
    Available at any point: mid-game or after competition ends.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_player
from models.competition import Competition
from models.player import Player
from schemas.analysis import PlayerAnalysisSchema
from services.analysis import generate_player_analysis

router = APIRouter()


@router.get(
    "/competitions/{code}/players/{player_id}/analysis",
    response_model=PlayerAnalysisSchema,
)
async def get_player_analysis(
    code: str,
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    """
    Generate a full analysis of a player's trading decisions.

    - Identifies trading style (scalper / day trader / swing trader / buy-and-hold)
    - Detects techniques (stop-loss discipline, pyramiding, averaging down, OCO, etc.)
    - Computes win rate, best/worst trade, hold times, diversification score
    - Provides narrative commentary, strengths, and improvement suggestions
    - Available mid-game (tracks live progress) or after end (final report)
    """
    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot view another player's analysis")

    result = await db.execute(
        select(Competition).where(Competition.lobby_code == code)
    )
    competition = result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail=f"Competition '{code}' not found")

    if current_player.competition_id != competition.id:
        raise HTTPException(status_code=403, detail="Player does not belong to this competition")

    return await generate_player_analysis(db, current_player, competition)
