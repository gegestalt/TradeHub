from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from models.enums import OrderSide, OrderStatus, OrderType


class OrderCreate(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    order_type: OrderType = OrderType.market
    side: OrderSide
    quantity: Decimal = Field(..., gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)


class OrderOut(BaseModel):
    id: str
    ticker: str
    order_type: OrderType
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    status: OrderStatus
    fill_price: Decimal | None
    fill_at: datetime | None
    fee_paid: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}
