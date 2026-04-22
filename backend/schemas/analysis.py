from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class RoundTripSchema(BaseModel):
    ticker: str
    buy_price: Decimal
    sell_price: Decimal | None
    quantity: Decimal
    realized_pnl: Decimal | None
    return_pct: Decimal | None
    hold_minutes: float | None
    still_open: bool


class PlayerAnalysisSchema(BaseModel):
    player_id: str
    competition_id: str
    generated_at: datetime

    # Volume
    total_orders_placed: int
    orders_filled: int
    orders_cancelled: int
    orders_expired: int
    round_trips_completed: int

    # P&L
    total_realized_pnl: Decimal
    total_fees_paid: Decimal
    best_trade_pnl: Decimal | None
    best_trade_ticker: str | None
    worst_trade_pnl: Decimal | None
    worst_trade_ticker: str | None
    win_rate_pct: float | None

    # Timing
    avg_hold_minutes: float | None

    # Classification
    trading_style: str
    risk_profile: str

    # Techniques
    techniques_identified: list[str]

    # Diversification
    tickers_traded: list[str]
    most_traded_ticker: str | None
    diversification_score: float

    # Order sophistication
    used_limit_orders: bool
    used_stop_loss: bool
    used_take_profit: bool
    used_oco: bool
    conditional_order_rate_pct: float

    # Narrative
    commentary: str
    strengths: list[str]
    improvements: list[str]

    # Detail
    round_trips: list[RoundTripSchema]
