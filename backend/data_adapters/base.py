from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from models.enums import DataSource


class OHLCV:
    __slots__ = ("ticker", "open", "high", "low", "close", "volume", "timestamp")

    def __init__(
        self,
        ticker: str,
        open: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: Decimal,
        timestamp: datetime,
    ) -> None:
        self.ticker = ticker
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.timestamp = timestamp


@runtime_checkable
class DataAdapter(Protocol):
    source: DataSource

    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal: ...
    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]: ...
    def list_tickers(self) -> list[str]: ...
