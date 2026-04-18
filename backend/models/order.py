import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.enums import OrderSide, OrderStatus, OrderType


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(String(36), ForeignKey("players.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20))
    order_type: Mapped[str] = mapped_column(Enum(OrderType), default=OrderType.market)
    side: Mapped[str] = mapped_column(Enum(OrderSide), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    status: Mapped[str] = mapped_column(Enum(OrderStatus), default=OrderStatus.pending)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fill_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fee_paid: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player", back_populates="orders")  # noqa: F821
