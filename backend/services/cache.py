"""Price cache — in-memory by default, Redis when REDIS_URL is configured.

The same interface is used regardless of backend so the rest of the codebase
never imports Redis directly.  To enable Redis:

    REDIS_URL=redis://localhost:6379/0

If the env var is absent or Redis is unreachable the system falls back to the
in-memory TTL dict transparently.  This means Redis is optional for local dev
and required only when running multiple uvicorn workers that need a shared cache.

Redis key layout
----------------
  price:{source}:{ticker}  →  price string, TTL set on write

Why Redis matters for multi-worker deployments
----------------------------------------------
With a single uvicorn process the in-memory dict is fine: all requests share
the same Python object.  With --workers N each process has its own dict, so
yfinance is called up to N times per TTL window.  Redis collapses that to a
single network round-trip shared across all workers.
"""

import logging
import time
from decimal import Decimal

_log = logging.getLogger(__name__)


# ── In-memory fallback ─────────────────────────────────────────────────────────

class _InMemoryCache:
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

    @property
    def backend(self) -> str:
        return "memory"


# ── Redis backend ──────────────────────────────────────────────────────────────

class _RedisCache:
    """Synchronous redis.Redis client wrapped with the same interface."""

    def __init__(self, client, ttl_seconds: int = 5) -> None:
        self._r = client
        self._ttl = ttl_seconds
        self._prefix = "price"

    def _key(self, ticker: str, source: str) -> str:
        return f"{self._prefix}:{source}:{ticker}"

    def get(self, ticker: str, source: str) -> Decimal | None:
        try:
            val = self._r.get(self._key(ticker, source))
            return Decimal(val.decode()) if val else None
        except Exception as exc:
            _log.warning("Redis get failed, treating as cache miss: %s", exc)
            return None

    def set(self, ticker: str, source: str, price: Decimal) -> None:
        try:
            self._r.setex(self._key(ticker, source), self._ttl, str(price))
        except Exception as exc:
            _log.warning("Redis set failed: %s", exc)

    def delete(self, ticker: str, source: str) -> None:
        try:
            self._r.delete(self._key(ticker, source))
        except Exception as exc:
            _log.warning("Redis delete failed: %s", exc)

    def clear(self) -> None:
        try:
            for key in self._r.scan_iter(f"{self._prefix}:*"):
                self._r.delete(key)
        except Exception as exc:
            _log.warning("Redis clear failed: %s", exc)

    @property
    def size(self) -> int:
        try:
            return sum(1 for _ in self._r.scan_iter(f"{self._prefix}:*"))
        except Exception:
            return -1

    @property
    def backend(self) -> str:
        return "redis"


# ── Factory ────────────────────────────────────────────────────────────────────

def _build_cache(ttl_seconds: int = 5):
    import os
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return _InMemoryCache(ttl_seconds)

    try:
        import redis  # type: ignore[import]
        client = redis.from_url(redis_url, socket_connect_timeout=2)
        client.ping()
        _log.info("Price cache: Redis at %s", redis_url)
        return _RedisCache(client, ttl_seconds)
    except Exception as exc:
        _log.warning(
            "Redis unavailable (%s) — falling back to in-memory price cache", exc
        )
        return _InMemoryCache(ttl_seconds)


price_cache = _build_cache()
