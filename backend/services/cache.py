"""In-memory TTL price cache.

Drop-in replaceable with Redis: swap _PriceCache for an async Redis client
that implements get(key) / set(key, value, ex=ttl) / delete(key).
"""

import time
from decimal import Decimal


class _PriceCache:
    def __init__(self, ttl_seconds: int = 5) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[Decimal, float]] = {}

    def get(self, ticker: str, source: str) -> Decimal | None:
        key = f"{source}:{ticker}"
        entry = self._store.get(key)
        if entry and time.monotonic() - entry[1] < self._ttl:
            return entry[0]
        return None

    def set(self, ticker: str, source: str, price: Decimal) -> None:
        self._store[f"{source}:{ticker}"] = (price, time.monotonic())

    def delete(self, ticker: str, source: str) -> None:
        self._store.pop(f"{source}:{ticker}", None)

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


price_cache = _PriceCache()
