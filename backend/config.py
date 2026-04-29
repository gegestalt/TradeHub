from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./tradehub.db"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    PRICE_SNAPSHOT_INTERVAL_SECONDS: int = 60
    ORDER_PROCESSOR_INTERVAL_SECONDS: int = 5
    ORDER_RATE_LIMIT: int = 10  # max orders per player per minute

    # Risk engine
    LIQUIDATION_CHECK_INTERVAL_SECONDS: int = 10
    MAINTENANCE_MARGIN_PCT: float = 0.25   # equity must stay above 25 % of position notional
    LIQUIDATION_FEE_PCT: float = 0.005     # extra fee charged on forced liquidation

    # Circuit breaker / Market Guardian
    CIRCUIT_BREAKER_THRESHOLD_PCT: float = 0.10   # pause if price moves > 10 % …
    CIRCUIT_BREAKER_WINDOW_SECONDS: int = 5        # … within this rolling window
    CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 60     # asset stays paused for 60 s


settings = Settings()
