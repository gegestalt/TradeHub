from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from config import settings
from data_adapters.offline import OfflineDataAdapter
from data_adapters.online import OnlineDataAdapter
from models.enums import DataSource

router = APIRouter()


def _get_adapter(source: DataSource):
    return (
        OnlineDataAdapter()
        if source == DataSource.online
        else OfflineDataAdapter(settings.DATA_DIR)
    )


@router.get("/{ticker}")
async def get_price(ticker: str, source: DataSource = DataSource.offline):
    adapter = _get_adapter(source)
    try:
        price = adapter.get_price(ticker.upper())
        return {"ticker": ticker.upper(), "price": str(price), "source": source}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{ticker}/stats")
async def get_price_stats(ticker: str, source: DataSource = DataSource.offline):
    """24-hour market statistics: open, high, low, close, volume, change."""
    adapter = _get_adapter(source)
    t = ticker.upper()
    try:
        now = datetime.now(tz=UTC)
        rows = adapter.get_ohlcv(t, now - timedelta(hours=24), now)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=404, detail=f"No OHLCV data for {t}")

    open_price = rows[0].open
    close_price = rows[-1].close
    high = max(r.high for r in rows)
    low = min(r.low for r in rows)
    volume = sum(r.volume for r in rows)
    change = close_price - open_price
    change_pct = (change / open_price * 100) if open_price else 0

    return {
        "ticker": t,
        "open_24h": str(open_price),
        "high_24h": str(high),
        "low_24h": str(low),
        "close": str(close_price),
        "volume_24h": str(volume),
        "change_24h": str(change),
        "change_pct_24h": f"{float(change_pct):.2f}",
        "source": source,
    }


@router.get("/{ticker}/history")
async def get_price_history(
    ticker: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
    source: DataSource = DataSource.offline,
):
    adapter = _get_adapter(source)
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
