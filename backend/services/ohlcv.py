"""OHLCV timeframe aggregation and resampling.

Converts a list of base OHLCV rows (any granularity) into a coarser timeframe
by bucket-grouping on the timestamp.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from data_adapters.base import OHLCV
from models.enums import Timeframe

_TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.m1: 1,
    Timeframe.m5: 5,
    Timeframe.m15: 15,
    Timeframe.m30: 30,
    Timeframe.h1: 60,
    Timeframe.h4: 240,
    Timeframe.d1: 1440,
    Timeframe.w1: 10080,
}


def _bucket(ts: datetime, minutes: int) -> datetime:
    """Floor timestamp to the nearest `minutes` boundary (UTC)."""
    ts_utc = ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
    epoch = ts_utc.timestamp()
    floored = (epoch // (minutes * 60)) * (minutes * 60)
    return datetime.fromtimestamp(floored, tz=UTC)


def resample(rows: list[OHLCV], timeframe: Timeframe) -> list[OHLCV]:
    """Aggregate `rows` into `timeframe` candles.

    Candles are sorted ascending by timestamp. Within each bucket:
      open  = first row's open
      high  = max of all highs
      low   = min of all lows
      close = last row's close
      volume = sum of all volumes
    """
    if not rows:
        return []

    minutes = _TIMEFRAME_MINUTES[timeframe]
    buckets: dict[datetime, list[OHLCV]] = {}
    for row in rows:
        key = _bucket(row.timestamp, minutes)
        buckets.setdefault(key, []).append(row)

    candles: list[OHLCV] = []
    for ts in sorted(buckets):
        group = buckets[ts]
        candles.append(
            OHLCV(
                ticker=group[0].ticker,
                open=group[0].open,
                high=max(r.high for r in group),
                low=min(r.low for r in group),
                close=group[-1].close,
                volume=sum((r.volume for r in group), Decimal("0")),
                timestamp=ts,
            )
        )
    return candles


def lookback_for(timeframe: Timeframe, limit: int) -> timedelta:
    """How far back to fetch raw data to produce `limit` candles of `timeframe`."""
    minutes = _TIMEFRAME_MINUTES[timeframe]
    return timedelta(minutes=minutes * limit * 2)  # 2× buffer for sparse data
