from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from data_adapters.base import OHLCV
from models.enums import DataSource

_ET = ZoneInfo("America/New_York")

# Crypto tickers trade 24/7 — detected by suffix
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-BTC", "-ETH", "-USDC")

# NYSE / NASDAQ market hours (Eastern Time)
_NYSE_OPEN = (9, 30)
_NYSE_CLOSE = (16, 0)

# US market holidays 2024-2026 (month, day) — fixed-date only
# Floating holidays (MLK Day, Presidents Day, etc.) are computed dynamically.
_FIXED_HOLIDAYS: set[tuple[int, int]] = {
    (1, 1),    # New Year's Day
    (6, 19),   # Juneteenth
    (7, 4),    # Independence Day
    (12, 25),  # Christmas Day
}


def _is_crypto(ticker: str) -> bool:
    t = ticker.upper()
    return any(t.endswith(sfx) for sfx in _CRYPTO_SUFFIXES)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    """Return the nth occurrence of weekday (0=Mon) in the given month."""
    first = datetime(year, month, 1, tzinfo=_ET)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + (n - 1) * 7)


def _good_friday(year: int) -> datetime:
    """Compute Good Friday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = datetime(year, month, day, tzinfo=_ET)
    return easter - timedelta(days=2)


def _us_market_holidays(year: int) -> set[tuple[int, int]]:
    holidays = set(_FIXED_HOLIDAYS)

    # New Year's Day observed (if Jan 1 is Saturday → Dec 31; if Sunday → Jan 2)
    jan1 = datetime(year, 1, 1, tzinfo=_ET)
    if jan1.weekday() == 5:  # Saturday
        holidays.add((12, 31))
    elif jan1.weekday() == 6:  # Sunday
        holidays.add((1, 2))

    # MLK Day — 3rd Monday of January
    mlk = _nth_weekday(year, 1, 0, 3)
    holidays.add((mlk.month, mlk.day))

    # Presidents' Day — 3rd Monday of February
    pres = _nth_weekday(year, 2, 0, 3)
    holidays.add((pres.month, pres.day))

    # Good Friday
    gf = _good_friday(year)
    holidays.add((gf.month, gf.day))

    # Memorial Day — last Monday of May
    mem = _nth_weekday(year, 5, 0, 5)
    if mem.month != 5:
        mem = _nth_weekday(year, 5, 0, 4)
    holidays.add((mem.month, mem.day))

    # Labor Day — 1st Monday of September
    lab = _nth_weekday(year, 9, 0, 1)
    holidays.add((lab.month, lab.day))

    # Thanksgiving — 4th Thursday of November
    thx = _nth_weekday(year, 11, 3, 4)
    holidays.add((thx.month, thx.day))

    # Christmas observed
    dec25 = datetime(year, 12, 25, tzinfo=_ET)
    if dec25.weekday() == 5:  # Saturday → Dec 24
        holidays.add((12, 24))
    elif dec25.weekday() == 6:  # Sunday → Dec 26
        holidays.add((12, 26))

    # Independence Day observed
    jul4 = datetime(year, 7, 4, tzinfo=_ET)
    if jul4.weekday() == 5:
        holidays.add((7, 3))
    elif jul4.weekday() == 6:
        holidays.add((7, 5))

    # Juneteenth observed
    jun19 = datetime(year, 6, 19, tzinfo=_ET)
    if jun19.weekday() == 5:
        holidays.add((6, 18))
    elif jun19.weekday() == 6:
        holidays.add((6, 20))

    return holidays


def _next_nyse_open(now: datetime) -> datetime:
    """Return the next NYSE open time after `now` (ET-aware)."""
    candidate = now.astimezone(_ET)
    for _ in range(10):
        candidate += timedelta(days=1)
        holidays = _us_market_holidays(candidate.year)
        if candidate.weekday() < 5 and (candidate.month, candidate.day) not in holidays:
            return candidate.replace(
                hour=_NYSE_OPEN[0], minute=_NYSE_OPEN[1], second=0, microsecond=0
            )
    return candidate  # fallback — should never reach here


class MarketStatus:
    __slots__ = ("is_open", "status", "next_open_at", "timezone")

    def __init__(
        self,
        is_open: bool,
        status: str,
        next_open_at: datetime | None,
        timezone: str,
    ) -> None:
        self.is_open = is_open
        self.status = status
        self.next_open_at = next_open_at
        self.timezone = timezone

    def to_dict(self) -> dict:
        return {
            "market_open": self.is_open,
            "market_status": self.status,
            "next_open_at": self.next_open_at.isoformat() if self.next_open_at else None,
            "timezone": self.timezone,
        }


def get_market_status(ticker: str, now: datetime | None = None) -> MarketStatus:
    """Return market open/closed status for the given ticker."""
    if now is None:
        now = datetime.now(tz=_ET)

    if _is_crypto(ticker):
        return MarketStatus(
            is_open=True,
            status="Crypto trades 24/7",
            next_open_at=None,
            timezone="UTC",
        )

    et_now = now.astimezone(_ET)
    holidays = _us_market_holidays(et_now.year)

    # Weekend
    if et_now.weekday() >= 5:
        days_until_monday = 7 - et_now.weekday()
        next_open = (et_now + timedelta(days=days_until_monday)).replace(
            hour=_NYSE_OPEN[0], minute=_NYSE_OPEN[1], second=0, microsecond=0
        )
        return MarketStatus(
            is_open=False,
            status=f"Market closed — weekend",
            next_open_at=next_open,
            timezone="America/New_York",
        )

    # Holiday
    if (et_now.month, et_now.day) in holidays:
        next_open = _next_nyse_open(et_now)
        return MarketStatus(
            is_open=False,
            status="Market closed — US market holiday",
            next_open_at=next_open,
            timezone="America/New_York",
        )

    # Pre-market
    market_open = et_now.replace(
        hour=_NYSE_OPEN[0], minute=_NYSE_OPEN[1], second=0, microsecond=0
    )
    market_close = et_now.replace(
        hour=_NYSE_CLOSE[0], minute=_NYSE_CLOSE[1], second=0, microsecond=0
    )

    if et_now < market_open:
        return MarketStatus(
            is_open=False,
            status="Market closed — pre-market",
            next_open_at=market_open,
            timezone="America/New_York",
        )

    if et_now >= market_close:
        next_open = _next_nyse_open(et_now)
        return MarketStatus(
            is_open=False,
            status="Market closed — after-hours",
            next_open_at=next_open,
            timezone="America/New_York",
        )

    return MarketStatus(
        is_open=True,
        status="Market is open (NYSE/NASDAQ regular hours)",
        next_open_at=None,
        timezone="America/New_York",
    )


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
        from data_adapters.asset_registry import list_tickers
        return list_tickers()
