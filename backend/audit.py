"""Structured audit trail — writes to both the DB and the Python logger.

DB writes (via _db_emit) are the source of truth for compliance queries.
Logger writes are for real-time observability and log aggregation.

All functions accept an optional `db` session. When provided, the event is
persisted to the audit_log table inside the caller's transaction (so it is
atomically committed or rolled back together with the balance change). When
db is None the event is logged only — this is acceptable for non-critical
paths (e.g. tests that call service functions without a full session).
"""

import json
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger("tradehub.audit")


def _serialize(v: object) -> str:
    return json.dumps(v, default=str)


def _log(event_type: str, **fields) -> None:
    _logger.info(_serialize({"ts": datetime.utcnow().isoformat(), "event": event_type, **fields}))


async def _db_emit(
    db: AsyncSession,
    event_type: str,
    player_id: str | None = None,
    competition_id: str | None = None,
    order_id: str | None = None,
    ticker: str | None = None,
    **payload_fields,
) -> None:
    from models.audit_log import AuditLog
    row = AuditLog(
        event_type=event_type,
        player_id=player_id,
        competition_id=competition_id,
        order_id=order_id,
        ticker=ticker,
        payload=_serialize(payload_fields),
    )
    db.add(row)


async def log_order_fill(
    *,
    db: AsyncSession,
    player_id: str,
    competition_id: str,
    order_id: str,
    ticker: str,
    side: str,
    quantity: Decimal,
    fill_price: Decimal,
    fee: Decimal,
    balance_before: Decimal,
    balance_after: Decimal,
) -> None:
    fields = dict(
        side=side, quantity=quantity, fill_price=fill_price,
        fee=fee, balance_before=balance_before, balance_after=balance_after,
    )
    _log("order_fill", player_id=player_id, order_id=order_id, ticker=ticker, **fields)
    await _db_emit(
        db, "order_fill",
        player_id=player_id, competition_id=competition_id,
        order_id=order_id, ticker=ticker, **fields,
    )


async def log_order_partial_fill(
    *,
    db: AsyncSession,
    player_id: str,
    competition_id: str,
    order_id: str,
    ticker: str,
    side: str,
    requested_qty: Decimal,
    filled_qty: Decimal,
    fill_price: Decimal,
    fee: Decimal,
    balance_before: Decimal,
    balance_after: Decimal,
) -> None:
    fields = dict(
        side=side, requested_qty=requested_qty, filled_qty=filled_qty,
        fill_price=fill_price, fee=fee,
        balance_before=balance_before, balance_after=balance_after,
    )
    _log("order_partial_fill", player_id=player_id, order_id=order_id, ticker=ticker, **fields)
    await _db_emit(
        db, "order_partial_fill",
        player_id=player_id, competition_id=competition_id,
        order_id=order_id, ticker=ticker, **fields,
    )


async def log_order_rejected(
    *,
    db: AsyncSession | None = None,
    player_id: str,
    order_id: str,
    ticker: str,
    reason: str,
) -> None:
    _log("order_rejected", player_id=player_id, order_id=order_id, ticker=ticker, reason=reason)
    if db is not None:
        await _db_emit(
            db, "order_rejected",
            player_id=player_id, order_id=order_id, ticker=ticker, reason=reason,
        )


async def log_position_change(
    *,
    db: AsyncSession | None = None,
    player_id: str,
    ticker: str,
    qty_before: Decimal,
    qty_after: Decimal,
    avg_entry_price: Decimal,
) -> None:
    fields = dict(qty_before=qty_before, qty_after=qty_after, avg_entry_price=avg_entry_price)
    _log("position_change", player_id=player_id, ticker=ticker, **fields)
    if db is not None:
        await _db_emit(db, "position_change", player_id=player_id, ticker=ticker, **fields)
