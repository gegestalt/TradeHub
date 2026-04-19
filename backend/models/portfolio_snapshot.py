import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class PortfolioSnapshot(Base):
    """Point-in-time portfolio value snapshot for a player.

    Written by the background snapshot task every PRICE_SNAPSHOT_INTERVAL_SECONDS.
    Used to power the P&L history chart and Sharpe ratio scoring.
    """

    __tablename__ = "portfolio_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    player_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=False, index=True
    )
    competition_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("competitions.id"), nullable=False, index=True
    )
    total_value: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    positions_value: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    player: Mapped["Player"] = relationship("Player", back_populates="portfolio_snapshots")  # noqa: F821
