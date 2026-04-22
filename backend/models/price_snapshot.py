import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.enums import PriceSource


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    competition_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("competitions.id"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(Enum(PriceSource), nullable=False)

    competition: Mapped["Competition"] = relationship(  # noqa: F821
        "Competition", back_populates="price_snapshots"
    )

    __table_args__ = (
        Index(
            "ix_price_snapshots_competition_ticker_time",
            "competition_id", "ticker", "recorded_at",
        ),
    )
