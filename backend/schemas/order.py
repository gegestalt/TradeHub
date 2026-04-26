from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from models.enums import OrderSide, OrderStatus, OrderType, TimeInForce


class OrderCreate(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    order_type: OrderType = OrderType.market
    side: OrderSide
    quantity: Decimal = Field(..., gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: TimeInForce = TimeInForce.gtc

    @model_validator(mode="after")
    def validate_prices(self) -> "OrderCreate":
        if self.order_type == OrderType.limit and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.order_type == OrderType.take_profit and self.take_profit_price is None:
            raise ValueError("take_profit order requires take_profit_price")
        if (
            self.order_type in (OrderType.stop_loss, OrderType.stop_limit)
            and self.stop_price is None
        ):
            raise ValueError(f"{self.order_type} order requires stop_price")
        if self.order_type == OrderType.stop_limit and self.limit_price is None:
            raise ValueError("stop_limit order requires both stop_price and limit_price")
        return self


class OCOCreate(BaseModel):
    """One-Cancels-Other: a stop-loss and take-profit on the same position."""

    ticker: str = Field(..., min_length=1, max_length=20)
    side: OrderSide
    quantity: Decimal = Field(..., gt=0)
    stop_price: Decimal = Field(..., gt=0)  # stop-loss trigger
    take_profit_price: Decimal = Field(..., gt=0)  # take-profit trigger


class OrderOut(BaseModel):
    id: str
    ticker: str
    order_type: OrderType
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    take_profit_price: Decimal | None
    time_in_force: TimeInForce
    oco_pair_id: str | None
    status: OrderStatus
    fill_price: Decimal | None
    fill_at: datetime | None
    fee_paid: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}
