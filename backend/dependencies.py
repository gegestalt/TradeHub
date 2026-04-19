from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from data_adapters.base import OHLCV, DataAdapter
from data_adapters.offline import OfflineDataAdapter
from data_adapters.online import OnlineDataAdapter
from database import get_db
from models.enums import DataSource, OrderStatus
from models.order import Order
from models.player import Player
from services.cache import price_cache


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


class _CachedAdapter:
    """Wraps any DataAdapter and caches get_price results via price_cache."""

    def __init__(self, inner: DataAdapter) -> None:
        self._inner = inner
        self.source = inner.source

    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal:
        if at is not None:
            return self._inner.get_price(ticker, at)
        cached = price_cache.get(ticker, str(self.source))
        if cached is not None:
            return cached
        price = self._inner.get_price(ticker)
        price_cache.set(ticker, str(self.source), price)
        return price

    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]:
        return self._inner.get_ohlcv(ticker, start, end)

    def list_tickers(self) -> list[str]:
        return self._inner.list_tickers()


def get_adapter(data_source: DataSource) -> DataAdapter:
    if data_source == DataSource.online:
        return _CachedAdapter(OnlineDataAdapter())
    return _CachedAdapter(OfflineDataAdapter(settings.DATA_DIR))
