import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.enums import OrderSide, OrderStatus, OrderType, TimeInForce


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20))
    order_type: Mapped[str] = mapped_column(Enum(OrderType), default=OrderType.market)
    side: Mapped[str] = mapped_column(Enum(OrderSide), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    time_in_force: Mapped[str] = mapped_column(Enum(TimeInForce), default=TimeInForce.gtc)
    # Links the two legs of an OCO order; both legs share the same oco_pair_id
    oco_pair_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(Enum(OrderStatus), default=OrderStatus.pending)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fill_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fee_paid: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player", back_populates="orders")  # noqa: F821

    __table_args__ = (
        Index("ix_orders_player_status", "player_id", "status"),
        Index("ix_orders_status_type", "status", "order_type"),
    )
