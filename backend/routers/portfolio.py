from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_adapter, get_current_player
from models.competition import Competition
from models.player import Player
from schemas.portfolio import PortfolioOut
from services.portfolio import get_portfolio

router = APIRouter()


@router.get("/players/{player_id}/portfolio", response_model=PortfolioOut)
async def player_portfolio(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot view another player's portfolio")

    comp_result = await db.execute(
        select(Competition).where(Competition.id == current_player.competition_id)
    )
    competition = comp_result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")

    adapter = get_adapter(competition.data_source)
    return await get_portfolio(db, current_player, adapter)
