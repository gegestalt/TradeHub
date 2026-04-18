from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./tradehub.db"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    PRICE_SNAPSHOT_INTERVAL_SECONDS: int = 60
    DATA_DIR: str = "../data"
    ORDER_RATE_LIMIT: int = 10  # max orders per player per minute


settings = Settings()
