import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Enum, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.enums import CompetitionState, DataSource, ScoringMethod


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255))
    lobby_code: Mapped[str] = mapped_column(String(6), unique=True, index=True)
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    state: Mapped[str] = mapped_column(
        Enum(CompetitionState), default=CompetitionState.lobby, nullable=False
    )
    asset_universe: Mapped[list] = mapped_column(JSON, nullable=False)
    data_source: Mapped[str] = mapped_column(
        Enum(DataSource), default=DataSource.offline, nullable=False
    )
    scoring_method: Mapped[str] = mapped_column(
        Enum(ScoringMethod), default=ScoringMethod.total_value, nullable=False
    )
    fee_pct: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0.001"))
    max_leverage: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("1.0"))
    allow_shorts: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    players: Mapped[list["Player"]] = relationship("Player", back_populates="competition")  # noqa: F821
    price_snapshots: Mapped[list["PriceSnapshot"]] = relationship(  # noqa: F821
        "PriceSnapshot", back_populates="competition"
    )
