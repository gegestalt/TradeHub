"""Competition-scoped OHLCV candle fetching.

Bounds the time window to the competition's lifecycle and routes through the
competition's own data adapter (online/offline/mock). This ensures an ended
competition's candles are capped at its end_at, and an offline competition
never leaks data outside its data directory.
"""

from datetime import UTC, datetime

from fastapi import HTTPException

from data_adapters.base import OHLCV
from data_adapters.factory import get_adapter
from models.competition import Competition
from models.enums import Timeframe
from services.ohlcv import lookback_for, resample


def get_competition_candles(
    competition: Competition,
    ticker: str,
    timeframe: Timeframe,
    limit: int,
) -> list[OHLCV]:
    """Return resampled candles for `ticker`, bounded by competition timeline.

    Time range:
      start — competition.start_at, or `lookback_for(timeframe, limit)` before end
      end   — competition.end_at if ended, otherwise now (never in the future)
    """
    adapter = get_adapter(competition)
    now = datetime.now(tz=UTC)

    end = _utc(competition.end_at) if competition.end_at else now
    if end > now:
        end = now

    start = (
        _utc(competition.start_at)
        if competition.start_at
        else end - lookback_for(timeframe, limit)
    )

    try:
        rows = adapter.get_ohlcv(ticker, start, end)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return resample(rows, timeframe)[-limit:]


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
