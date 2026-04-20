"""Mock data adapter — synthetic random-walk prices for dev and testing.

Each live competition gets its own adapter instance (keyed by competition_id)
so prices are stable and consistent across all requests within a competition.
Prices advance by one random-walk tick on every snapshot cycle.
"""

import random
import threading
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from data_adapters.base import OHLCV
from models.enums import DataSource

_DP2 = Decimal("0.01")

_registry: dict[str, "MockDataAdapter"] = {}
_lock = threading.Lock()


class MockDataAdapter:
    source = DataSource.mock

    def __init__(self, tickers: list[str], seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._prices: dict[str, Decimal] = {
            t: Decimal(str(round(self._rng.uniform(50.0, 200.0), 2)))
            for t in tickers
        }
        self._history: dict[str, list[tuple[int, Decimal]]] = {t: [] for t in tickers}
        self._tick_num: int = 0

    # ── Registry helpers ──────────────────────────────────────────────────────

    @classmethod
    def for_competition(cls, competition_id: str, tickers: list[str]) -> "MockDataAdapter":
        """Return the singleton adapter for this competition, creating it if needed."""
        with _lock:
            if competition_id not in _registry:
                seed = abs(hash(competition_id)) % (2**31)
                _registry[competition_id] = cls(tickers=tickers, seed=seed)
            adapter = _registry[competition_id]
            # Register any new tickers added since creation
            for ticker in tickers:
                if ticker not in adapter._prices:
                    adapter._prices[ticker] = Decimal(
                        str(round(adapter._rng.uniform(50.0, 200.0), 2))
                    )
                    adapter._history[ticker] = []
            return adapter

    @classmethod
    def evict(cls, competition_id: str) -> None:
        """Remove a competition's adapter from the registry (call on competition end)."""
        with _lock:
            _registry.pop(competition_id, None)

    @classmethod
    def registry_size(cls) -> int:
        with _lock:
            return len(_registry)

    # ── Price movement ────────────────────────────────────────────────────────

    def tick(self, volatility: float = 0.015) -> None:
        """Advance all prices by one random-walk step. Called by snapshot task."""
        self._tick_num += 1
        for ticker in list(self._prices):
            pct = Decimal(str(round(self._rng.gauss(0, volatility), 6)))
            new_price = self._prices[ticker] * (1 + pct)
            new_price = max(Decimal("0.01"), new_price).quantize(_DP2, rounding=ROUND_HALF_UP)
            self._prices[ticker] = new_price
            self._history[ticker].append((self._tick_num, new_price))

    # ── DataAdapter protocol ──────────────────────────────────────────────────

    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal:
        if ticker not in self._prices:
            self._prices[ticker] = Decimal(str(round(self._rng.uniform(50.0, 200.0), 2)))
            self._history[ticker] = []
        return self._prices[ticker]

    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]:
        return []

    def list_tickers(self) -> list[str]:
        return list(self._prices.keys())

    # ── Inspection helpers ────────────────────────────────────────────────────

    def price_history(self, ticker: str) -> list[tuple[int, Decimal]]:
        return list(self._history.get(ticker, []))

    def current_prices(self) -> dict[str, Decimal]:
        return dict(self._prices)

    @property
    def tick_num(self) -> int:
        return self._tick_num
