from datetime import datetime
from decimal import Decimal

from data_adapters.base import OHLCV
from models.enums import DataSource


class OnlineDataAdapter:
    source = DataSource.online

    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal:
        import yfinance as yf

        if at is not None:
            hist = yf.download(ticker, start=at.date(), auto_adjust=True, progress=False)
            if hist.empty:
                raise ValueError(f"No data for ticker '{ticker}' at {at}")
            close_col = hist["Close"].squeeze()
            return Decimal(str(float(close_col.iloc[-1])))

        info = yf.Ticker(ticker).fast_info
        price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
        if price is None:
            raise ValueError(f"Could not fetch live price for '{ticker}'")
        return Decimal(str(float(price)))

    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]:
        import yfinance as yf

        hist = yf.download(
            ticker, start=start.date(), end=end.date(), auto_adjust=True, progress=False
        )
        if hist.empty:
            return []

        # yfinance ≥0.2 may return MultiLevel columns; squeeze to flat Series per column
        hist.columns = (
            hist.columns.get_level_values(0)
            if hasattr(hist.columns, "levels")
            else hist.columns
        )
        rows = []
        for ts, row in hist.iterrows():
            rows.append(
                OHLCV(
                    ticker=ticker,
                    open=Decimal(str(float(row["Open"]))),
                    high=Decimal(str(float(row["High"]))),
                    low=Decimal(str(float(row["Low"]))),
                    close=Decimal(str(float(row["Close"]))),
                    volume=Decimal(str(float(row["Volume"]))),
                    timestamp=ts.to_pydatetime(),
                )
            )
        return rows

    def list_tickers(self) -> list[str]:
        return []
