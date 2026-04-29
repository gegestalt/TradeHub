"""Circuit Breaker / Market Guardian.

Detects abnormal price movements and automatically pauses trading on an
asset to protect competitors from "bad data" spikes.

Rule: if a ticker's price moves more than CIRCUIT_BREAKER_THRESHOLD_PCT
(default 10 %) within CIRCUIT_BREAKER_WINDOW_SECONDS (default 5 s), trading
for that ticker is suspended for CIRCUIT_BREAKER_COOLDOWN_SECONDS (default 60 s).

The guardian is per-competition so each game has independent circuit breakers.

Usage
-----
    guardian = get_guardian(competition_id)
    guardian.record_price("AAPL", Decimal("150.00"))

    if guardian.is_paused("AAPL"):
        raise HTTPException(400, "Trading in AAPL is temporarily paused ...")

    # record every price the adapter fetches before filling an order
"""

import logging
import time
from collections import defaultdict, deque
from decimal import Decimal

from config import settings

_log = logging.getLogger(__name__)

# Registry: competition_id → MarketGuardian
_guardians: dict[str, "MarketGuardian"] = {}


class MarketGuardian:
    """Tracks price history and enforces circuit breakers per ticker."""

    def __init__(
        self,
        spike_threshold_pct: float | None = None,
        window_seconds: int | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        self._threshold = (spike_threshold_pct if spike_threshold_pct is not None
                           else settings.CIRCUIT_BREAKER_THRESHOLD_PCT)
        self._window = (window_seconds if window_seconds is not None
                        else settings.CIRCUIT_BREAKER_WINDOW_SECONDS)
        self._cooldown = (cooldown_seconds if cooldown_seconds is not None
                          else settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS)

        # ticker → deque of (timestamp, price) within the rolling window
        self._history: dict[str, deque] = defaultdict(deque)
        # ticker → timestamp when the pause expires (0 = not paused)
        self._paused_until: dict[str, float] = {}

    # ── Public interface ───────────────────────────────────────────────────────

    def record_price(self, ticker: str, price: Decimal) -> bool:
        """Record a new price observation. Returns True if a circuit trip occurred."""
        now = time.monotonic()
        hist = self._history[ticker]

        # Evict observations outside the rolling window
        while hist and now - hist[0][0] > self._window:
            hist.popleft()

        hist.append((now, price))

        if self._should_trip(ticker, hist):
            self._trip(ticker, now)
            return True
        return False

    def is_paused(self, ticker: str) -> bool:
        """Return True if trading in this ticker is currently suspended."""
        deadline = self._paused_until.get(ticker, 0.0)
        if deadline and time.monotonic() < deadline:
            return True
        # Auto-clear expired pauses
        if ticker in self._paused_until:
            del self._paused_until[ticker]
        return False

    def paused_assets(self) -> list[str]:
        """Return tickers currently under a circuit-breaker pause."""
        now = time.monotonic()
        return [t for t, dl in list(self._paused_until.items()) if now < dl]

    def manual_reset(self, ticker: str) -> None:
        """Allow an administrator to manually clear a circuit-breaker pause."""
        self._paused_until.pop(ticker, None)
        _log.info("Circuit breaker manually reset for %s", ticker)

    def price_history(self, ticker: str) -> list[tuple[float, Decimal]]:
        """Return the current price history buffer for a ticker (for debugging)."""
        return list(self._history.get(ticker, []))

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _should_trip(self, ticker: str, hist: deque) -> bool:
        if len(hist) < 2:
            return False
        prices = [p for _, p in hist]
        lo, hi = min(prices), max(prices)
        if lo == Decimal("0"):
            return False
        movement = float((hi - lo) / lo)
        return movement > self._threshold

    def _trip(self, ticker: str, now: float) -> None:
        self._paused_until[ticker] = now + self._cooldown
        _log.warning(
            "CIRCUIT BREAKER TRIPPED for %s — trading paused for %ds",
            ticker, self._cooldown,
        )


# ── Registry helpers ───────────────────────────────────────────────────────────

def get_guardian(competition_id: str) -> MarketGuardian:
    """Return the MarketGuardian for a competition, creating it if needed."""
    if competition_id not in _guardians:
        _guardians[competition_id] = MarketGuardian()
    return _guardians[competition_id]


def evict_guardian(competition_id: str) -> None:
    """Remove a competition's guardian when the game ends."""
    _guardians.pop(competition_id, None)
