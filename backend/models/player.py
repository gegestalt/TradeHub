import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    competition_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("competitions.id"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(100))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    spectator: Mapped[bool] = mapped_column(Boolean, default=False)
    is_creator: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optimistic-locking counter. SQLAlchemy increments this on every UPDATE and
    # checks it in the WHERE clause, so a concurrent writer on a different process
    # (or different DB connection) gets StaleDataError instead of silently
    # overwriting the balance. Pair with per-process asyncio lock for full safety.
    balance_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Set to True by the Reconciler when cash_balance drifts from the ledger.
    # Trading is blocked for the player until an admin clears the flag.
    trading_halted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    competition: Mapped["Competition"] = relationship("Competition", back_populates="players")  # noqa: F821
    user: Mapped["User | None"] = relationship("User", back_populates="players")  # noqa: F821
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="player")  # noqa: F821
    positions: Mapped[list["Position"]] = relationship("Position", back_populates="player")  # noqa: F821
    alerts: Mapped[list["PriceAlert"]] = relationship("PriceAlert", back_populates="player")  # noqa: F821
    watchlist: Mapped[list["WatchlistItem"]] = relationship(  # noqa: F821
        "WatchlistItem", back_populates="player"
    )
    portfolio_snapshots: Mapped[list["PortfolioSnapshot"]] = relationship(  # noqa: F821
        "PortfolioSnapshot", back_populates="player"
    )

    __mapper_args__ = {"version_id_col": balance_version}

    __table_args__ = (
        UniqueConstraint("competition_id", "user_id", name="uq_player_competition_user"),
        Index("ix_players_competition_spectator", "competition_id", "spectator"),
    )
