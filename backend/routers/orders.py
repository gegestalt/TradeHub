from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import check_order_rate_limit, get_adapter, get_current_player
from models.competition import Competition
from models.enums import CompetitionState, OrderStatus
from models.order import Order
from models.player import Player
from schemas.order import OCOCreate, OrderCreate, OrderOut
from services import order_engine

router = APIRouter()


@router.post("/players/{player_id}/orders", response_model=OrderOut, status_code=201)
async def place_order(
    player_id: str,
    data: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(check_order_rate_limit),
):
    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot place orders for another player")

    if current_player.spectator:
        raise HTTPException(status_code=403, detail="Spectators cannot place orders")

    comp_result = await db.execute(
        select(Competition).where(Competition.id == current_player.competition_id)
    )
    competition = comp_result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")

    if competition.state != CompetitionState.active:
        raise HTTPException(status_code=400, detail="Competition is not active")

    adapter = get_adapter(competition)
    order = await order_engine.place_order(db, current_player, competition, data, adapter)
    return OrderOut.model_validate(order)


@router.post("/players/{player_id}/orders/oco", response_model=list[OrderOut], status_code=201)
async def place_oco_order(
    player_id: str,
    data: OCOCreate,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(check_order_rate_limit),
):
    """Place a One-Cancels-Other order: stop-loss + take-profit pair."""
    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot place orders for another player")

    if current_player.spectator:
        raise HTTPException(status_code=403, detail="Spectators cannot place orders")

    comp_result = await db.execute(
        select(Competition).where(Competition.id == current_player.competition_id)
    )
    competition = comp_result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")

    adapter = get_adapter(competition)
    sl, tp = await order_engine.place_oco_order(db, current_player, competition, data, adapter)
    return [OrderOut.model_validate(sl), OrderOut.model_validate(tp)]


@router.get("/players/{player_id}/orders", response_model=list[OrderOut])
async def list_orders(
    player_id: str,
    status: OrderStatus | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot view another player's orders")

    stmt = select(Order).where(Order.player_id == player_id).order_by(Order.created_at.desc())
    if status is not None:
        stmt = stmt.where(Order.status == status)

    result = await db.execute(stmt)
    return [OrderOut.model_validate(o) for o in result.scalars().all()]


@router.delete("/players/{player_id}/orders/{order_id}", response_model=OrderOut)
async def cancel_order(
    player_id: str,
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot cancel another player's orders")

    order = await order_engine.cancel_order(db, current_player, order_id)
    return OrderOut.model_validate(order)
