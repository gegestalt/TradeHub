"""Immutable, DB-persisted audit trail for all balance-mutating events.

Every order fill, rejection, and position change writes a row here.
The table is append-only by convention — no row is ever updated or deleted.
This gives you:
  - A queryable audit trail for compliance
  - A foundation for event sourcing (each row IS an event)
  - Recovery: if the in-memory state diverges, replay AuditLog to reconstruct

Event types
-----------
order_fill      — order matched and balance/position updated
order_rejected  — order was blocked (insufficient funds, invalid ticker, etc.)
position_change — position quantity changed (buy, sell, or short cover)
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    player_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    competition_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # JSON payload: all event-specific fields stored as a text blob so the schema
    # never needs to change when new event types are introduced.
    payload: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_audit_log_player_id", "player_id"),
        Index("ix_audit_log_event_type", "event_type"),
        Index("ix_audit_log_recorded_at", "recorded_at"),
    )
