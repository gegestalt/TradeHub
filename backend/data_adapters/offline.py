"""Offline data adapter — reads OHLCV data from CSV or JSON files.

Time-aware mode: when competition_start_at is provided, get_price maps elapsed
real time onto the historical data range so all players see the same "current"
price at any moment during the competition.

  elapsed = now - competition_start_at
  effective_time = first_row.timestamp + elapsed

This means at t=0 everyone sees the first row's close; as the competition
progresses they walk forward through the historical data at 1:1 real-time speed.
When elapsed exceeds the data range the last available price is returned.
"""

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from data_adapters.base import OHLCV
from models.enums import DataSource


class OfflineDataAdapter:
    source = DataSource.offline

    def __init__(
        self,
        data_dir: str = "../data",
        competition_start_at: datetime | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._cache: dict[str, list[OHLCV]] = {}
        self._competition_start_at = competition_start_at

    # ── DataAdapter protocol ──────────────────────────────────────────────────

    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal:
        rows = self._load_ticker(ticker)
        if not rows:
            raise ValueError(f"No data for ticker '{ticker}'")

        # Explicit timestamp wins (used by backfill / historical queries)
        if at is not None:
            return self._price_at(rows, at)

        # Time-aware: map elapsed competition time onto historical data
        if self._competition_start_at is not None:
            elapsed = datetime.utcnow() - self._competition_start_at
            effective_time = rows[0].timestamp + elapsed
            return self._price_at(rows, effective_time)

        # Fallback: latest row (used when no competition context, e.g. analytics)
        return rows[-1].close

    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]:
        rows = self._load_ticker(ticker)
        return [r for r in rows if start <= r.timestamp <= end]

    def list_tickers(self) -> list[str]:
        if not self._data_dir.exists():
            return []
        return [p.stem for p in self._data_dir.iterdir() if p.suffix in (".csv", ".json")]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def data_range(self, ticker: str) -> tuple[datetime, datetime] | None:
        """Return (first_timestamp, last_timestamp) for a ticker, or None if no data."""
        try:
            rows = self._load_ticker(ticker)
        except ValueError:
            return None
        if not rows:
            return None
        return rows[0].timestamp, rows[-1].timestamp

    def _price_at(self, rows: list[OHLCV], at: datetime) -> Decimal:
        """Return close price of the last row whose timestamp <= at."""
        for row in reversed(rows):
            if row.timestamp <= at:
                return row.close
        return rows[0].close

    def _load_ticker(self, ticker: str) -> list[OHLCV]:
        if ticker in self._cache:
            return self._cache[ticker]

        for ext in ("csv", "json"):
            path = self._data_dir / f"{ticker}.{ext}"
            if path.exists():
                loader = self._load_csv if ext == "csv" else self._load_json
                rows = loader(path, ticker)
                self._cache[ticker] = rows
                return rows

        raise ValueError(f"No offline data found for ticker '{ticker}'")

    def _load_csv(self, path: Path, ticker: str) -> list[OHLCV]:
        rows = []
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    rows.append(
                        OHLCV(
                            ticker=ticker,
                            open=Decimal(str(row.get("Open") or row.get("open") or "0")),
                            high=Decimal(str(row.get("High") or row.get("high") or "0")),
                            low=Decimal(str(row.get("Low") or row.get("low") or "0")),
                            close=Decimal(str(row.get("Close") or row.get("close") or "0")),
                            volume=Decimal(str(row.get("Volume") or row.get("volume") or "0")),
                            timestamp=self._parse_date(row),
                        )
                    )
                except (ValueError, KeyError):
                    continue
        return sorted(rows, key=lambda r: r.timestamp)

    def _load_json(self, path: Path, ticker: str) -> list[OHLCV]:
        with open(path) as f:
            data = json.load(f)
        rows = []
        for entry in data:
            try:
                rows.append(
                    OHLCV(
                        ticker=ticker,
                        open=Decimal(str(entry.get("open", 0))),
                        high=Decimal(str(entry.get("high", 0))),
                        low=Decimal(str(entry.get("low", 0))),
                        close=Decimal(str(entry.get("close", 0))),
                        volume=Decimal(str(entry.get("volume", 0))),
                        timestamp=self._parse_date(entry),
                    )
                )
            except (ValueError, KeyError):
                continue
        return sorted(rows, key=lambda r: r.timestamp)

    @staticmethod
    def _parse_date(row: dict) -> datetime:
        for key in ("Date", "date", "Datetime", "datetime", "timestamp", "Timestamp"):
            val = row.get(key)
            if val:
                return datetime.fromisoformat(str(val).split("+")[0].strip().rstrip("Z"))
        raise ValueError(f"No date field in row: {list(row.keys())}")
