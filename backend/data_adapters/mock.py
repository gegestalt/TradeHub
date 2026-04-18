import random
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from data_adapters.base import OHLCV
from models.enums import DataSource

_DP2 = Decimal("0.01")


class MockDataAdapter:
    """Synthetic random-walk adapter for simulation and deterministic testing.

    Call tick() to advance all prices by one step. Price history is recorded
    so momentum strategies can query past values.
    """

    source = DataSource.offline

    def __init__(
        self,
        tickers: list[str],
        seed: int = 42,
        base_price: float = 100.0,
    ) -> None:
        self._rng = random.Random(seed)
        self._prices: dict[str, Decimal] = {
            t: Decimal(str(round(self._rng.uniform(base_price * 0.5, base_price * 2.0), 2)))
            for t in tickers
        }
        self._history: dict[str, list[tuple[int, Decimal]]] = {t: [] for t in tickers}
        self._tick_num: int = 0

    def tick(self, volatility: float = 0.02) -> None:
        self._tick_num += 1
        for ticker in self._prices:
            pct = Decimal(str(round(self._rng.gauss(0, volatility), 6)))
            new_price = self._prices[ticker] * (1 + pct)
            new_price = max(Decimal("0.01"), new_price).quantize(_DP2, rounding=ROUND_HALF_UP)
            self._prices[ticker] = new_price
            self._history[ticker].append((self._tick_num, new_price))

    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal:
        if ticker not in self._prices:
            raise ValueError(f"Unknown ticker '{ticker}' — available: {list(self._prices)}")
        return self._prices[ticker]

    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]:
        return []

    def list_tickers(self) -> list[str]:
        return list(self._prices.keys())

    def price_history(self, ticker: str) -> list[tuple[int, Decimal]]:
        return list(self._history.get(ticker, []))

    def current_prices(self) -> dict[str, Decimal]:
        return dict(self._prices)
