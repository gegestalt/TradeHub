import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    competition_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("competitions.id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(100))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    spectator: Mapped[bool] = mapped_column(Boolean, default=False)
    is_creator: Mapped[bool] = mapped_column(Boolean, default=False)

    competition: Mapped["Competition"] = relationship("Competition", back_populates="players")  # noqa: F821
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="player")  # noqa: F821
    positions: Mapped[list["Position"]] = relationship("Position", back_populates="player")  # noqa: F821
