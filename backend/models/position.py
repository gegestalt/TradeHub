import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))  # negative = short
    avg_entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player", back_populates="positions")  # noqa: F821

    __table_args__ = (Index("ix_positions_player_ticker", "player_id", "ticker"),)
