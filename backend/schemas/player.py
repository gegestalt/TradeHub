from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class PositionOut(BaseModel):
    id: str
    ticker: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    opened_at: datetime

    model_config = {"from_attributes": True}


class PlayerPortfolio(BaseModel):
    player_id: str
    display_name: str
    cash_balance: Decimal
    positions: list[PositionOut]
    total_value: Decimal
