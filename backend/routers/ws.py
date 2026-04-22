"""WebSocket endpoint for real-time competition data.

Streams prices, leaderboard, and order-fill events to connected clients.
Each competition code maps to a broadcast channel with a single shared push task,
so broadcast cost stays O(clients) not O(clients²).
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from database import AsyncSessionLocal
from dependencies import get_adapter
from models.competition import Competition
from models.enums import CompetitionState
from services import competition as competition_service
from services.websocket_manager import (
    leaderboard_message,
    manager,
    price_tick_message,
    prices_message,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_STREAM_INTERVAL = 3  # seconds between broadcast cycles

# One push task per active competition room; keyed by lobby code.
# A new task is started when the first client joins and cancelled when the last leaves.
_room_tasks: dict[str, asyncio.Task] = {}


@router.websocket("/ws/{code}")
async def competition_ws(code: str, ws: WebSocket):
    """Stream prices + leaderboard to all clients in a competition room."""
    await manager.connect(ws, code)
    try:
        # Send initial snapshot immediately on connect
        await _broadcast_snapshot(code)

        # Ensure exactly one shared push loop runs per room
        if code not in _room_tasks or _room_tasks[code].done():
            _room_tasks[code] = asyncio.create_task(_push_loop(code))

        while True:
            # Receive keeps the connection alive; text frames are ignored for now
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws, code)
        # Cancel the room task when the last client leaves
        if manager.count(code) == 0:
            task = _room_tasks.pop(code, None)
            if task and not task.done():
                task.cancel()


async def _push_loop(code: str) -> None:
    """Shared background task — one per room — that broadcasts snapshots periodically."""
    while True:
        await asyncio.sleep(_STREAM_INTERVAL)
        await _broadcast_snapshot(code)


async def _broadcast_snapshot(code: str) -> None:
    """Fetch current prices + leaderboard and broadcast to competition channel."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Competition).where(Competition.lobby_code == code)
            )
            competition = result.scalar_one_or_none()
            if competition is None or competition.state != CompetitionState.active:
                return

            adapter = get_adapter(competition)

            # Prices for every ticker in the universe
            price_data = {}
            for ticker in competition.asset_universe:
                with contextlib.suppress(Exception):
                    price_data[ticker] = adapter.get_price(ticker.upper())

            if price_data:
                # Bulk snapshot — all tickers in one message (for screener / overview)
                await manager.broadcast(code, prices_message(price_data))

                # Per-ticker ticks — each carries a timestamp so the frontend can
                # update the open candle on the chart without a REST round-trip
                now = datetime.now(tz=UTC)
                for ticker, price in price_data.items():
                    await manager.broadcast(
                        code, price_tick_message(ticker, price, now)
                    )

            # Leaderboard
            entries = await competition_service.get_leaderboard(db, code, adapter)
            lb_payload = leaderboard_message([e.model_dump() for e in entries])
            await manager.broadcast(code, lb_payload)

    except Exception:
        logger.exception("ws: broadcast error for competition %s", code)
