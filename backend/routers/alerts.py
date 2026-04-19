"""Price alert endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_player
from models.player import Player
from schemas.alert import AlertCreate, AlertOut
from services.alerts import create_alert, delete_alert, list_alerts

router = APIRouter(prefix="/players/{player_id}/alerts", tags=["alerts"])


@router.post("", response_model=AlertOut, status_code=201)
async def add_alert(
    player_id: str,
    data: AlertCreate,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    alert = await create_alert(db, current_player, data)
    return AlertOut.model_validate(alert)


@router.get("", response_model=list[AlertOut])
async def get_alerts(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    return [AlertOut.model_validate(a) for a in await list_alerts(db, current_player)]


@router.delete("/{alert_id}", status_code=204)
async def remove_alert(
    player_id: str,
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    await delete_alert(db, current_player, alert_id)
