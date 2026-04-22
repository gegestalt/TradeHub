from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from models.enums import CompetitionState, DataSource


class LobbyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    creator_name: str = Field(..., min_length=1, max_length=100)
    asset_universe: list[str] = Field(..., min_length=1, description="Tickers players can trade")
    starting_balance: Decimal = Field(default=Decimal("10000"), gt=0)
    max_players: int = Field(default=10, ge=2, le=100)
    duration_minutes: int | None = Field(default=None, gt=0, description="Game length in minutes")
    data_source: DataSource = DataSource.online

    @field_validator("asset_universe")
    @classmethod
    def normalise_tickers(cls, v: list[str]) -> list[str]:
        tickers = [t.upper().strip() for t in v]
        if len(set(tickers)) != len(tickers):
            raise ValueError("asset_universe contains duplicate tickers")
        return tickers


class LobbyOut(BaseModel):
    id: str
    lobby_code: str
    name: str
    asset_universe: list[str]
    starting_balance: Decimal
    max_players: int
    duration_minutes: int | None
    state: CompetitionState
    created_at: datetime

    model_config = {"from_attributes": True}
