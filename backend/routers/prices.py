from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from config import settings
from data_adapters.offline import OfflineDataAdapter
from data_adapters.online import OnlineDataAdapter
from models.enums import DataSource

router = APIRouter()


@router.get("/{ticker}")
async def get_price(ticker: str, source: DataSource = DataSource.offline):
    adapter = (
        OnlineDataAdapter() if source == DataSource.online else OfflineDataAdapter(settings.DATA_DIR)
    )
    try:
        price = adapter.get_price(ticker.upper())
        return {"ticker": ticker.upper(), "price": str(price), "source": source}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{ticker}/history")
async def get_price_history(
    ticker: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
    source: DataSource = DataSource.offline,
):
    adapter = (
        OnlineDataAdapter() if source == DataSource.online else OfflineDataAdapter(settings.DATA_DIR)
    )
    try:
        rows = adapter.get_ohlcv(ticker.upper(), start, end)
        return {
            "ticker": ticker.upper(),
            "data": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "open": str(r.open),
                    "high": str(r.high),
                    "low": str(r.low),
                    "close": str(r.close),
                    "volume": str(r.volume),
                }
                for r in rows
            ],
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
