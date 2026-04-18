import asyncio
from collections import defaultdict

# Per-player asyncio locks prevent balance and position races under concurrent requests
# within a single process. For multi-worker deployments, replace with SELECT FOR UPDATE
# (PostgreSQL) or a distributed lock (Redis).
_player_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_player_lock(player_id: str) -> asyncio.Lock:
    return _player_locks[player_id]
