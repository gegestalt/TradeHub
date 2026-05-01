"""Order idempotency layer.

Prevents duplicate fills when a network hiccup causes the client to retry
a "Buy" request that already succeeded.

Protocol
--------
The client includes a unique X-Idempotency-Key header (UUID) on every order
POST.  The server:

  1. Checks the store for  player_id + key.
  2. If found   → returns the cached OrderOut JSON immediately (HTTP 200).
  3. If missing → executes the trade normally, then stores the result for TTL.

The TTL (default 24 h) must exceed the maximum plausible retry window.

Backend
-------
Same pattern as services/cache.py: Redis when REDIS_URL is set, in-memory
otherwise.  In-memory is fine for single-process; Redis is required for
multi-worker deployments so that retries that land on a different worker still
hit the cache.

Usage (in routers/orders.py)
-----------------------------
    from services.idempotency import get_cached_order, cache_order_result

    if x_idempotency_key:
        cached = get_cached_order(current_player.id, x_idempotency_key)
        if cached:
            return JSONResponse(cached, status_code=200)

    order = await order_engine.place_order(...)
    result = OrderOut.model_validate(order).model_dump(mode="json")

    if x_idempotency_key:
        cache_order_result(current_player.id, x_idempotency_key, result)
"""

import json
import logging
import time
from typing import Any

from config import settings

_log = logging.getLogger(__name__)

_TTL = settings.IDEMPOTENCY_TTL_SECONDS


# ── In-memory store ────────────────────────────────────────────────────────────

class _InMemoryIdempotencyStore:
    def __init__(self, ttl_seconds: int = _TTL) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[Any, float]] = {}

    def _key(self, player_id: str, idempotency_key: str) -> str:
        return f"{player_id}:{idempotency_key}"

    def get(self, player_id: str, idempotency_key: str) -> dict | None:
        k = self._key(player_id, idempotency_key)
        entry = self._store.get(k)
        if entry:
            payload, ts = entry
            if time.monotonic() - ts < self._ttl:
                return payload
            del self._store[k]
        return None

    def set(self, player_id: str, idempotency_key: str, payload: dict) -> None:
        self._store[self._key(player_id, idempotency_key)] = (payload, time.monotonic())

    def size(self) -> int:
        return len(self._store)

    @property
    def backend(self) -> str:
        return "memory"


# ── Redis store ────────────────────────────────────────────────────────────────

class _RedisIdempotencyStore:
    def __init__(self, client, ttl_seconds: int = _TTL) -> None:
        self._r = client
        self._ttl = ttl_seconds
        self._prefix = "idempotency"

    def _key(self, player_id: str, idempotency_key: str) -> str:
        return f"{self._prefix}:{player_id}:{idempotency_key}"

    def get(self, player_id: str, idempotency_key: str) -> dict | None:
        try:
            raw = self._r.get(self._key(player_id, idempotency_key))
            return json.loads(raw) if raw else None
        except Exception as exc:
            _log.warning("Idempotency Redis get failed: %s", exc)
            return None

    def set(self, player_id: str, idempotency_key: str, payload: dict) -> None:
        try:
            self._r.setex(
                self._key(player_id, idempotency_key),
                self._ttl,
                json.dumps(payload, default=str),
            )
        except Exception as exc:
            _log.warning("Idempotency Redis set failed: %s", exc)

    @property
    def backend(self) -> str:
        return "redis"


# ── Factory ────────────────────────────────────────────────────────────────────

def _build_store() -> _InMemoryIdempotencyStore | _RedisIdempotencyStore:
    import os
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis
            client = redis.from_url(redis_url, socket_connect_timeout=2)
            client.ping()
            _log.info("Idempotency store: Redis at %s", redis_url)
            return _RedisIdempotencyStore(client, _TTL)
        except Exception as exc:
            _log.warning("Redis unavailable for idempotency store (%s) — using memory", exc)
    return _InMemoryIdempotencyStore(_TTL)


_store = _build_store()


# ── Public API ─────────────────────────────────────────────────────────────────

def get_cached_order(player_id: str, idempotency_key: str) -> dict | None:
    """Return the previously cached OrderOut payload, or None if not found."""
    return _store.get(player_id, idempotency_key)


def cache_order_result(player_id: str, idempotency_key: str, payload: dict) -> None:
    """Store the OrderOut payload so future retries get the same result."""
    _store.set(player_id, idempotency_key, payload)


def store_backend() -> str:
    return _store.backend
