import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from data_adapters.base import OHLCV
from models.enums import DataSource


class OfflineDataAdapter:
    source = DataSource.offline

    def __init__(self, data_dir: str = "../data") -> None:
        self._data_dir = Path(data_dir)
        self._cache: dict[str, list[OHLCV]] = {}

    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal:
        rows = self._load_ticker(ticker)
        if not rows:
            raise ValueError(f"No data for ticker '{ticker}'")

        if at is None:
            return rows[-1].close

        for row in reversed(rows):
            if row.timestamp <= at:
                return row.close

        return rows[0].close

    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]:
        rows = self._load_ticker(ticker)
        return [r for r in rows if start <= r.timestamp <= end]

    def list_tickers(self) -> list[str]:
        if not self._data_dir.exists():
            return []
        return [p.stem for p in self._data_dir.iterdir() if p.suffix in (".csv", ".json")]

    def _load_ticker(self, ticker: str) -> list[OHLCV]:
        if ticker in self._cache:
            return self._cache[ticker]

        for ext in ("csv", "json"):
            path = self._data_dir / f"{ticker}.{ext}"
            if path.exists():
                rows = self._load_csv(path, ticker) if ext == "csv" else self._load_json(path, ticker)
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
                # Strip timezone suffix for fromisoformat compatibility
                return datetime.fromisoformat(str(val).split("+")[0].strip().rstrip("Z"))
        raise ValueError(f"No date field in row: {list(row.keys())}")
