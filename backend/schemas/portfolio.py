from decimal import Decimal

from pydantic import BaseModel


class PositionDetail(BaseModel):
    ticker: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_pct: Decimal
    side: str  # "long" or "short"


class PortfolioOut(BaseModel):
    cash_balance: Decimal
    positions_value: Decimal
    total_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    positions: list[PositionDetail]


class TradeOut(BaseModel):
    order_id: str
    player_id: str
    display_name: str
    ticker: str
    side: str
    quantity: Decimal
    fill_price: Decimal
    fee_paid: Decimal
    fill_at: str
