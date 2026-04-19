"""Analytics endpoints: indicators, order book depth, screener, multi-timeframe OHLCV."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from data_adapters.offline import OfflineDataAdapter
from data_adapters.online import OnlineDataAdapter
from database import get_db
from models.competition import Competition
from models.enums import (
    CompetitionState,
    DataSource,
    OrderSide,
    OrderStatus,
    OrderType,
    Timeframe,
)
from models.order import Order
from services.indicators import compute_all
from services.ohlcv import lookback_for, resample

router = APIRouter()


def _get_adapter(source: DataSource):
    return (
        OnlineDataAdapter()
        if source == DataSource.online
        else OfflineDataAdapter(settings.DATA_DIR)
    )


# ── Multi-timeframe OHLCV ─────────────────────────────────────────────────────


@router.get("/prices/{ticker}/candles")
async def get_candles(
    ticker: str,
    timeframe: Timeframe = Timeframe.d1,
    limit: int = Query(default=200, ge=1, le=1000),
    source: DataSource = DataSource.offline,
):
    """Resampled OHLCV candles for any supported timeframe (1m → 1w)."""
    adapter = _get_adapter(source)
    t = ticker.upper()
    now = datetime.now(tz=UTC)
    lookback = lookback_for(timeframe, limit)
    try:
        rows = adapter.get_ohlcv(t, now - lookback, now)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for {t}")

    candles = resample(rows, timeframe)[-limit:]

    return {
        "ticker": t,
        "timeframe": timeframe,
        "candles": [
            {
                "t": c.timestamp.isoformat(),
                "o": str(c.open),
                "h": str(c.high),
                "l": str(c.low),
                "c": str(c.close),
                "v": str(c.volume),
            }
            for c in candles
        ],
    }


# ── Technical Indicators ──────────────────────────────────────────────────────


@router.get("/prices/{ticker}/indicators")
async def get_indicators(
    ticker: str,
    source: DataSource = DataSource.offline,
    days: int = Query(default=200, ge=30, le=365),
):
    """RSI, MACD, EMA, SMA, Bollinger Bands, ATR, Stochastic, VWAP, OBV."""
    adapter = _get_adapter(source)
    t = ticker.upper()
    now = datetime.now(tz=UTC)
    try:
        rows = adapter.get_ohlcv(t, now - timedelta(days=days), now)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=404, detail=f"No OHLCV data for {t}")

    closes = [Decimal(str(r.close)) for r in rows]
    highs = [Decimal(str(r.high)) for r in rows]
    lows = [Decimal(str(r.low)) for r in rows]
    volumes = [Decimal(str(r.volume)) for r in rows]

    indicators = compute_all(closes, highs=highs, lows=lows, volumes=volumes)
    ts = [r.timestamp.isoformat() for r in rows[-100:]]

    return {"ticker": t, "timestamps": ts, "indicators": indicators}


# ── Order Book Depth ──────────────────────────────────────────────────────────


@router.get("/competitions/{code}/orderbook/{ticker}")
async def get_order_book(
    code: str,
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    """Aggregated bid/ask depth from pending limit orders in a competition."""
    result = await db.execute(
        select(Competition).where(Competition.lobby_code == code)
    )
    competition = result.scalar_one_or_none()
    if competition is None:
        raise HTTPException(status_code=404, detail="Competition not found")
    if competition.state != CompetitionState.active:
        raise HTTPException(status_code=400, detail="Competition is not active")

    t = ticker.upper()
    if t not in [u.upper() for u in competition.asset_universe]:
        raise HTTPException(
            status_code=400, detail=f"Ticker '{t}' not in competition asset universe"
        )

    from models.player import Player

    player_ids_result = await db.execute(
        select(Player.id).where(Player.competition_id == competition.id)
    )
    player_ids = [r[0] for r in player_ids_result.all()]

    orders_result = await db.execute(
        select(Order).where(
            Order.ticker == t,
            Order.order_type == OrderType.limit,
            Order.status == OrderStatus.pending,
            Order.player_id.in_(player_ids),
        )
    )
    pending = orders_result.scalars().all()

    bids: dict[str, dict] = {}
    asks: dict[str, dict] = {}

    for order in pending:
        if order.limit_price is None:
            continue
        pk = str(order.limit_price)
        if order.side == OrderSide.buy:
            if pk not in bids:
                bids[pk] = {"price": pk, "quantity": Decimal("0"), "order_count": 0}
            bids[pk]["quantity"] += order.quantity
            bids[pk]["order_count"] += 1
        else:
            if pk not in asks:
                asks[pk] = {"price": pk, "quantity": Decimal("0"), "order_count": 0}
            asks[pk]["quantity"] += order.quantity
            asks[pk]["order_count"] += 1

    sorted_bids = sorted(bids.values(), key=lambda x: Decimal(x["price"]), reverse=True)
    sorted_asks = sorted(asks.values(), key=lambda x: Decimal(x["price"]))

    for level in sorted_bids + sorted_asks:
        level["quantity"] = str(level["quantity"])

    # Spread and mid price
    best_bid = Decimal(sorted_bids[0]["price"]) if sorted_bids else None
    best_ask = Decimal(sorted_asks[0]["price"]) if sorted_asks else None
    spread = str(best_ask - best_bid) if best_bid and best_ask else None
    mid = str((best_bid + best_ask) / 2) if best_bid and best_ask else None

    return {
        "ticker": t,
        "competition_code": code,
        "bids": sorted_bids[:20],
        "asks": sorted_asks[:20],
        "best_bid": str(best_bid) if best_bid else None,
        "best_ask": str(best_ask) if best_ask else None,
        "spread": spread,
        "mid_price": mid,
    }


# ── Screener ──────────────────────────────────────────────────────────────────


@router.get("/competitions/{code}/screener")
async def screener(
    code: str,
    source: DataSource = DataSource.offline,
    db: AsyncSession = Depends(get_db),
):
    """Screen all tickers in a competition: price, 24h change, RSI, volume.

    Inspired by TradingView's screener — lets players identify opportunities
    across the competition's asset universe at a glance.
    """
    from services.indicators import rsi as calc_rsi

    result = await db.execute(
        select(Competition).where(Competition.lobby_code == code)
    )
    competition = result.scalar_one_or_none()
    if competition is None:
        raise HTTPException(status_code=404, detail="Competition not found")

    adapter = _get_adapter(source)
    now = datetime.now(tz=UTC)
    rows_out = []

    for ticker in competition.asset_universe:
        t = ticker.upper()
        try:
            price = adapter.get_price(t)
            rows = adapter.get_ohlcv(t, now - timedelta(days=30), now)
        except Exception:
            rows_out.append({"ticker": t, "error": "data unavailable"})
            continue

        if not rows:
            rows_out.append({"ticker": t, "error": "no data"})
            continue

        closes = [Decimal(str(r.close)) for r in rows]
        volumes = [Decimal(str(r.volume)) for r in rows]
        prev_close = closes[-2] if len(closes) > 1 else closes[-1]
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else Decimal("0")
        avg_vol = sum(volumes[-10:]) / min(10, len(volumes))

        rsi_series = calc_rsi(closes, 14)
        rsi_val = next((v for v in reversed(rsi_series) if v is not None), None)

        rows_out.append(
            {
                "ticker": t,
                "price": str(price),
                "change_24h": str(change),
                "change_pct_24h": f"{float(change_pct):.2f}",
                "rsi_14": str(rsi_val) if rsi_val is not None else None,
                "avg_volume_10d": str(avg_vol),
                "volume_today": str(volumes[-1]) if volumes else None,
            }
        )

    return {"competition_code": code, "tickers": rows_out}
