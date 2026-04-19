from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator

from models.enums import AlertCondition


class AlertCreate(BaseModel):
    ticker: str
    condition: AlertCondition
    price: Decimal

    @field_validator("ticker")
    @classmethod
    def ticker_upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("price")
    @classmethod
    def price_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("price must be positive")
        return v


class AlertOut(BaseModel):
    id: str
    ticker: str
    condition: AlertCondition
    price: Decimal
    triggered: bool
    triggered_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
