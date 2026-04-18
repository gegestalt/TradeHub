from datetime import datetime, timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from data_adapters.base import DataAdapter
from data_adapters.offline import OfflineDataAdapter
from data_adapters.online import OnlineDataAdapter
from database import get_db
from models.enums import DataSource, OrderStatus
from models.order import Order
from models.player import Player


async def get_current_player(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Player:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    result = await db.execute(select(Player).where(Player.token == token))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return player


async def check_order_rate_limit(
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
) -> Player:
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
    result = await db.execute(
        select(func.count()).where(
            Order.player_id == player.id,
            Order.created_at >= one_minute_ago,
            Order.status != OrderStatus.cancelled,
        )
    )
    count = result.scalar_one()
    if count >= settings.ORDER_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {settings.ORDER_RATE_LIMIT} orders per minute",
        )
    return player


def get_adapter(data_source: DataSource) -> DataAdapter:
    if data_source == DataSource.online:
        return OnlineDataAdapter()
    return OfflineDataAdapter(settings.DATA_DIR)
