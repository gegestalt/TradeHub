"""WebSocket endpoint for real-time competition data.

Streams prices, leaderboard, and order-fill events to connected clients.
Each competition code maps to a broadcast channel.
"""

import asyncio
import contextlib
import logging

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
    prices_message,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_STREAM_INTERVAL = 3  # seconds between broadcast cycles


@router.websocket("/ws/{code}")
async def competition_ws(code: str, ws: WebSocket):
    """Stream prices + leaderboard to all clients in a competition room."""
    await manager.connect(ws, code)
    try:
        # Send initial snapshot immediately
        await _broadcast_snapshot(code)

        # Start a per-connection push task while listening for client frames
        push_task = asyncio.create_task(_push_loop(code))
        try:
            while True:
                # Receive keeps the connection alive; text frames are ignored for now
                await ws.receive_text()
        finally:
            push_task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws, code)


async def _push_loop(code: str) -> None:
    """Background task that broadcasts snapshots every _STREAM_INTERVAL seconds."""
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

            adapter = get_adapter(competition.data_source)

            # Prices for every ticker in the universe
            price_data = {}
            for ticker in competition.asset_universe:
                with contextlib.suppress(Exception):
                    price_data[ticker] = adapter.get_price(ticker.upper())

            if price_data:
                await manager.broadcast(code, prices_message(price_data))

            # Leaderboard
            entries = await competition_service.get_leaderboard(db, code, adapter)
            lb_payload = leaderboard_message([e.model_dump() for e in entries])
            await manager.broadcast(code, lb_payload)

    except Exception:
        logger.exception("ws: broadcast error for competition %s", code)
