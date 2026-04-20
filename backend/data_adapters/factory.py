import math
from datetime import datetime
from decimal import Decimal

from config import settings
from data_adapters.base import OHLCV, DataAdapter
from data_adapters.mock import MockDataAdapter
from data_adapters.offline import OfflineDataAdapter
from data_adapters.online import OnlineDataAdapter
from models.competition import Competition
from models.enums import DataSource
from services.cache import price_cache


class CachedAdapter:
    def __init__(self, inner: DataAdapter) -> None:
        self._inner = inner
        self.source = inner.source

    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal:
        if at is not None:
            return self._inner.get_price(ticker, at)
        if self.source == DataSource.mock:
            return self._inner.get_price(ticker)
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


def get_adapter(competition: Competition) -> DataAdapter:
    source = DataSource(competition.data_source)

    if source == DataSource.online:
        return CachedAdapter(OnlineDataAdapter())

    if source == DataSource.mock:
        inner = MockDataAdapter.for_competition(competition.id, list(competition.asset_universe))
        return CachedAdapter(inner)

    return CachedAdapter(
        OfflineDataAdapter(settings.DATA_DIR, competition_start_at=competition.start_at)
    )
