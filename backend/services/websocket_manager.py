"""WebSocket connection manager.

Handles per-competition channels. Each competition has a set of connected
WebSocket clients. Messages are broadcast to all clients in a competition.

Swap the in-memory dict for Redis Pub/Sub to support multi-process deployments.
"""

import contextlib
import json
import logging
from collections import defaultdict
from decimal import Decimal

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # competition_code → set of active WebSocket connections
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, ws: WebSocket, code: str) -> None:
        await ws.accept()
        self._connections[code].add(ws)
        logger.debug("ws: client connected to competition %s (total=%d)", code, self.count(code))

    def disconnect(self, ws: WebSocket, code: str) -> None:
        self._connections[code].discard(ws)
        logger.debug("ws: client left competition %s (total=%d)", code, self.count(code))

    def count(self, code: str) -> int:
        return len(self._connections[code])

    async def broadcast(self, code: str, payload: dict) -> None:
        """Send a JSON message to all clients in the competition."""
        dead: list[WebSocket] = []
        text = json.dumps(payload, default=str)
        for ws in list(self._connections[code]):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, code)

    async def send_personal(self, ws: WebSocket, payload: dict) -> None:
        """Send a JSON message to a single client."""
        with contextlib.suppress(Exception):
            await ws.send_text(json.dumps(payload, default=str))


manager = ConnectionManager()


# ── Message builders ──────────────────────────────────────────────────────────


def price_message(ticker: str, price: Decimal) -> dict:
    return {"type": "price", "ticker": ticker, "price": str(price)}


def prices_message(prices: dict[str, Decimal]) -> dict:
    return {"type": "prices", "data": {t: str(p) for t, p in prices.items()}}


def order_filled_message(
    order_id: str,
    ticker: str,
    side: str,
    quantity: Decimal,
    fill_price: Decimal,
    fee: Decimal,
) -> dict:
    return {
        "type": "order_filled",
        "order_id": order_id,
        "ticker": ticker,
        "side": side,
        "quantity": str(quantity),
        "fill_price": str(fill_price),
        "fee": str(fee),
    }


def leaderboard_message(entries: list[dict]) -> dict:
    return {"type": "leaderboard", "data": entries}


def trade_message(
    player_id: str,
    display_name: str,
    ticker: str,
    side: str,
    quantity: Decimal,
    fill_price: Decimal,
) -> dict:
    return {
        "type": "trade",
        "player_id": player_id,
        "display_name": display_name,
        "ticker": ticker,
        "side": side,
        "quantity": str(quantity),
        "fill_price": str(fill_price),
    }
