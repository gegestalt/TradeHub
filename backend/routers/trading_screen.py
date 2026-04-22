"""Trading screen endpoints.

These three endpoints collectively power the main player dashboard:
  - Competition-scoped candles  — price history chart for any timeframe
  - Competition benchmark       — competition-average P&L overlay line
  - Trade marks                 — player's own buy/sell markers on the chart
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_player
from models.enums import OrderStatus, Timeframe
from models.order import Order
from models.player import Player
from services import competition as competition_service
from services.benchmark_service import get_competition_benchmark
from services.candle_service import get_competition_candles

router = APIRouter(tags=["trading-screen"])


@router.get("/competitions/{code}/prices/{ticker}/candles")
async def competition_candles(
    code: str,
    ticker: str,
    timeframe: Timeframe = Timeframe.d1,
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """OHLCV candles for a ticker, using the competition's data source and timeline.

    Unlike the standalone /prices/{ticker}/candles endpoint, this one:
      - Uses the competition's adapter (online/offline/mock)
      - Restricts the time range to the competition's start_at → end_at window
      - Validates the ticker is in the competition's asset universe
    """
    competition = await competition_service.get_competition(db, code)
    t = ticker.upper()

    if t not in [u.upper() for u in competition.asset_universe]:
        raise HTTPException(
            status_code=400,
            detail=f"Ticker '{t}' is not in this competition's asset universe",
        )

    candles = get_competition_candles(competition, t, timeframe, limit)
    return {
        "ticker": t,
        "competition_code": code,
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


@router.get("/competitions/{code}/benchmark")
async def competition_benchmark(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """Average portfolio performance across all players over time.

    Returns a time-series of the competition's mean portfolio return as a
    percentage change from starting_balance. Clients overlay this on the
    player's individual P&L chart as a comparison reference line — analogous
    to ^GSPC on TradingView's comparison feature.
    """
    competition = await competition_service.get_competition(db, code)
    series = await get_competition_benchmark(
        db, competition.id, competition.starting_balance
    )
    return {"competition_code": code, "series": series}


@router.get("/competitions/{code}/players/{player_id}/trade-marks/{ticker}")
async def player_trade_marks(
    code: str,
    player_id: str,
    ticker: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    """Filled orders for a player/ticker, formatted as chart overlay annotations.

    Returns buy/sell markers at exact fill prices so the frontend can render
    entry and exit points on the candle chart — matching TradingView's trade
    execution overlay. Only the authenticated player can see their own marks.
    """
    if current_player.id != player_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot access another player's trade marks",
        )

    await competition_service.get_competition(db, code)
    t = ticker.upper()

    result = await db.execute(
        select(Order)
        .where(
            Order.player_id == player_id,
            Order.ticker == t,
            Order.status == OrderStatus.filled,
            Order.fill_at.is_not(None),
        )
        .order_by(Order.fill_at.asc())
    )
    orders = result.scalars().all()

    return {
        "ticker": t,
        "competition_code": code,
        "player_id": player_id,
        "marks": [
            {
                "fill_at": o.fill_at.isoformat(),
                "fill_price": str(o.fill_price),
                "side": o.side,
                "quantity": str(o.quantity),
                "fee_paid": str(o.fee_paid),
                "order_type": o.order_type,
            }
            for o in orders
        ],
    }
