from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
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
from services.idempotency import cache_order_result, get_cached_order

router = APIRouter()


async def _get_competition_and_validate_player(
    code: str,
    player_id: str,
    current_player: Player,
    db: AsyncSession,
) -> Competition:
    """Load competition by lobby code and ensure the current player belongs to it."""
    result = await db.execute(
        select(Competition).where(Competition.lobby_code == code)
    )
    competition = result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail=f"Competition '{code}' not found")

    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot place orders for another player")

    if current_player.competition_id != competition.id:
        raise HTTPException(status_code=403, detail="Player does not belong to this competition")

    return competition


@router.post(
    "/competitions/{code}/players/{player_id}/orders",
    response_model=OrderOut,
    status_code=201,
)
async def place_order(
    code: str,
    player_id: str,
    data: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(check_order_rate_limit),
    x_idempotency_key: str | None = Header(default=None),
):
    if current_player.spectator:
        raise HTTPException(status_code=403, detail="Spectators cannot place orders")

    # Idempotency check — return cached result for duplicate requests.
    # HTTP 200 (not 201) signals "already processed, no new order created".
    if x_idempotency_key:
        cached = get_cached_order(current_player.id, x_idempotency_key)
        if cached:
            return JSONResponse(content=cached, status_code=200)

    competition = await _get_competition_and_validate_player(code, player_id, current_player, db)

    if competition.state != CompetitionState.active:
        raise HTTPException(status_code=400, detail="Competition is not active")

    adapter = get_adapter(competition)
    order = await order_engine.place_order(db, current_player, competition, data, adapter)
    result = OrderOut.model_validate(order)

    if x_idempotency_key:
        cache_order_result(current_player.id, x_idempotency_key, result.model_dump(mode="json"))

    return result


@router.post(
    "/competitions/{code}/players/{player_id}/orders/oco",
    response_model=list[OrderOut],
    status_code=201,
)
async def place_oco_order(
    code: str,
    player_id: str,
    data: OCOCreate,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(check_order_rate_limit),
):
    """Place a One-Cancels-Other order: stop-loss + take-profit pair."""
    if current_player.spectator:
        raise HTTPException(status_code=403, detail="Spectators cannot place orders")

    competition = await _get_competition_and_validate_player(code, player_id, current_player, db)

    if competition.state != CompetitionState.active:
        raise HTTPException(status_code=400, detail="Competition is not active")

    adapter = get_adapter(competition)
    sl, tp = await order_engine.place_oco_order(db, current_player, competition, data, adapter)
    return [OrderOut.model_validate(sl), OrderOut.model_validate(tp)]


@router.get(
    "/competitions/{code}/players/{player_id}/orders",
    response_model=list[OrderOut],
)
async def list_orders(
    code: str,
    player_id: str,
    status: OrderStatus | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    await _get_competition_and_validate_player(code, player_id, current_player, db)

    stmt = (
        select(Order)
        .where(Order.player_id == player_id)
        .order_by(Order.created_at.desc())
    )
    if status is not None:
        stmt = stmt.where(Order.status == status)

    result = await db.execute(stmt)
    return [OrderOut.model_validate(o) for o in result.scalars().all()]


@router.delete(
    "/competitions/{code}/players/{player_id}/orders/{order_id}",
    response_model=OrderOut,
)
async def cancel_order(
    code: str,
    player_id: str,
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    await _get_competition_and_validate_player(code, player_id, current_player, db)

    order = await order_engine.cancel_order(db, current_player, order_id)
    return OrderOut.model_validate(order)
