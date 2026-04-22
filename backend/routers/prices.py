from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from data_adapters.online import OnlineDataAdapter

router = APIRouter()


@router.get("/{ticker}")
async def get_price(ticker: str):
    adapter = OnlineDataAdapter()
    try:
        price = adapter.get_price(ticker.upper())
        return {"ticker": ticker.upper(), "price": str(price), "source": "online"}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{ticker}/stats")
async def get_price_stats(ticker: str):
    """24-hour market statistics: open, high, low, close, volume, change."""
    adapter = OnlineDataAdapter()
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
        "source": "online",
    }


@router.get("/{ticker}/history")
async def get_price_history(
    ticker: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
):
    adapter = OnlineDataAdapter()
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
