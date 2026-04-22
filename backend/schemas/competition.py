from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from models.enums import CompetitionState, DataSource, ScoringMethod


class CompetitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    starting_balance: Decimal = Field(default=Decimal("10000"), gt=0)
    start_at: datetime | None = None
    end_at: datetime | None = None
    asset_universe: list[str] = Field(..., min_length=1)
    data_source: DataSource = DataSource.online
    scoring_method: ScoringMethod = ScoringMethod.total_value
    fee_pct: Decimal = Field(default=Decimal("0.001"), ge=0, le=1)
    max_leverage: Decimal = Field(default=Decimal("1.0"), ge=1)
    allow_shorts: bool = False
    max_players: int = Field(default=10, ge=2, le=100)
    duration_minutes: int | None = Field(default=None, gt=0)
    creator_name: str = Field(..., min_length=1, max_length=100)


class CompetitionOut(BaseModel):
    id: str
    name: str
    lobby_code: str
    starting_balance: Decimal
    start_at: datetime | None
    end_at: datetime | None
    state: CompetitionState
    asset_universe: list[str]
    data_source: DataSource
    scoring_method: ScoringMethod
    fee_pct: Decimal
    max_leverage: Decimal
    allow_shorts: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class JoinRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    spectator: bool = False


class JoinResponse(BaseModel):
    player_id: str
    token: str
    display_name: str
    cash_balance: Decimal


class PositionSummary(BaseModel):
    ticker: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal


class LeaderboardEntry(BaseModel):
    rank: int
    player_id: str
    display_name: str
    cash_balance: Decimal
    positions_value: Decimal
    total_value: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    orders_filled: int = 0
    positions: list[PositionSummary] = []
    score: Decimal = Decimal("0")  # total_value or Sharpe ratio depending on scoring_method
