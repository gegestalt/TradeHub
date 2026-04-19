"""Technical indicator calculations.

All functions operate on plain lists of Decimals (closing prices or OHLCV rows).
No external dependencies — pure Python arithmetic.
"""

from decimal import ROUND_HALF_UP, Decimal

_DP = Decimal("0.00001")


def _q(v: Decimal) -> Decimal:
    return v.quantize(_DP, rounding=ROUND_HALF_UP)


# ── Moving averages ───────────────────────────────────────────────────────────


def sma(prices: list[Decimal], period: int) -> list[Decimal | None]:
    """Simple Moving Average. Returns None for the first (period-1) values."""
    result: list[Decimal | None] = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        result.append(_q(sum(window) / period))
    return result


def ema(prices: list[Decimal], period: int) -> list[Decimal | None]:
    """Exponential Moving Average using the standard smoothing factor 2/(period+1)."""
    if len(prices) < period:
        return [None] * len(prices)

    k = Decimal(2) / Decimal(period + 1)
    result: list[Decimal | None] = [None] * (period - 1)

    # Seed with SMA of first window
    seed = _q(sum(prices[:period]) / period)
    result.append(seed)
    prev = seed

    for price in prices[period:]:
        val = _q(price * k + prev * (1 - k))
        result.append(val)
        prev = val

    return result


# ── RSI ───────────────────────────────────────────────────────────────────────


def rsi(prices: list[Decimal], period: int = 14) -> list[Decimal | None]:
    """Relative Strength Index (Wilder's smoothing method)."""
    if len(prices) < period + 1:
        return [None] * len(prices)

    result: list[Decimal | None] = [None] * period
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    gains = [max(c, Decimal("0")) for c in changes]
    losses = [abs(min(c, Decimal("0"))) for c in changes]

    # Initial averages over first period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi_from(ag: Decimal, al: Decimal) -> Decimal:
        if al == 0:
            return Decimal("100")
        rs = ag / al
        return _q(Decimal("100") - Decimal("100") / (1 + rs))

    result.append(_rsi_from(avg_gain, avg_loss))

    for i in range(period, len(changes)):
        avg_gain = _q((avg_gain * (period - 1) + gains[i]) / period)
        avg_loss = _q((avg_loss * (period - 1) + losses[i]) / period)
        result.append(_rsi_from(avg_gain, avg_loss))

    return result


# ── MACD ──────────────────────────────────────────────────────────────────────


def macd(
    prices: list[Decimal],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[Decimal | None]]:
    """MACD indicator.

    Returns:
        macd_line: EMA(fast) − EMA(slow)
        signal_line: EMA(signal) of macd_line
        histogram: macd_line − signal_line
    """
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)

    macd_line: list[Decimal | None] = []
    for f, s in zip(ema_fast, ema_slow, strict=True):
        macd_line.append(_q(f - s) if f is not None and s is not None else None)

    # EMA of non-None MACD values
    macd_values = [v for v in macd_line if v is not None]
    if len(macd_values) < signal:
        signal_line: list[Decimal | None] = [None] * len(macd_line)
    else:
        raw_signal = ema(macd_values, signal)
        # Pad signal_line to align with macd_line
        none_count = len(macd_line) - len(macd_values)
        signal_line = [None] * none_count + raw_signal  # type: ignore[operator]

    histogram: list[Decimal | None] = [
        _q(m - s) if m is not None and s is not None else None
        for m, s in zip(macd_line, signal_line, strict=True)
    ]

    return {"macd_line": macd_line, "signal_line": signal_line, "histogram": histogram}


# ── Bollinger Bands ───────────────────────────────────────────────────────────


def bollinger_bands(
    prices: list[Decimal], period: int = 20, num_std: int = 2
) -> dict[str, list[Decimal | None]]:
    """Bollinger Bands: middle (SMA), upper, lower."""
    middle = sma(prices, period)
    upper: list[Decimal | None] = []
    lower: list[Decimal | None] = []

    for i, mid in enumerate(middle):
        if mid is None:
            upper.append(None)
            lower.append(None)
            continue
        window = prices[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((p - mean) ** 2 for p in window) / period
        std = _q(variance**Decimal("0.5"))
        upper.append(_q(mid + num_std * std))
        lower.append(_q(mid - num_std * std))

    return {"upper": upper, "middle": middle, "lower": lower}


# ── ATR ───────────────────────────────────────────────────────────────────────


def atr(
    high: list[Decimal],
    low: list[Decimal],
    close: list[Decimal],
    period: int = 14,
) -> list[Decimal | None]:
    """Average True Range using Wilder's smoothing."""
    n = len(close)
    if n < period + 1:
        return [None] * n

    trs: list[Decimal] = []
    for i in range(1, n):
        hl = high[i] - low[i]
        hpc = abs(high[i] - close[i - 1])
        lpc = abs(low[i] - close[i - 1])
        trs.append(max(hl, hpc, lpc))

    result: list[Decimal | None] = [None] * period  # first close has no TR
    avg = _q(sum(trs[:period]) / period)
    result.append(avg)
    for tr in trs[period:]:
        avg = _q((avg * (period - 1) + tr) / period)
        result.append(avg)
    return result


# ── Stochastic ────────────────────────────────────────────────────────────────


def stochastic(
    high: list[Decimal],
    low: list[Decimal],
    close: list[Decimal],
    k_period: int = 14,
    d_period: int = 3,
) -> dict[str, list[Decimal | None]]:
    """Stochastic oscillator. Returns %K and %D (SMA of %K)."""
    n = len(close)
    k_values: list[Decimal | None] = [None] * (k_period - 1)

    for i in range(k_period - 1, n):
        window_high = max(high[i - k_period + 1 : i + 1])
        window_low = min(low[i - k_period + 1 : i + 1])
        if window_high == window_low:
            k_values.append(Decimal("50"))
        else:
            k_values.append(_q((close[i] - window_low) / (window_high - window_low) * 100))

    k_non_none = [v for v in k_values if v is not None]
    d_raw = sma(k_non_none, d_period) if len(k_non_none) >= d_period else [None] * len(k_non_none)
    none_count = len(k_values) - len(k_non_none)
    d_values: list[Decimal | None] = [None] * none_count + d_raw  # type: ignore[operator]

    return {"k": k_values, "d": d_values}


# ── VWAP ──────────────────────────────────────────────────────────────────────


def vwap(
    high: list[Decimal],
    low: list[Decimal],
    close: list[Decimal],
    volume: list[Decimal],
) -> list[Decimal]:
    """Cumulative VWAP from the start of the series (session VWAP approximation)."""
    result: list[Decimal] = []
    cum_tp_vol = Decimal("0")
    cum_vol = Decimal("0")
    for hi, lo, c, v in zip(high, low, close, volume, strict=True):
        typical_price = (hi + lo + c) / 3
        cum_tp_vol += typical_price * v
        cum_vol += v
        result.append(_q(cum_tp_vol / cum_vol) if cum_vol > 0 else _q(typical_price))
    return result


# ── OBV ───────────────────────────────────────────────────────────────────────


def obv(close: list[Decimal], volume: list[Decimal]) -> list[Decimal]:
    """On-Balance Volume."""
    result: list[Decimal] = [Decimal("0")]
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            result.append(result[-1] + volume[i])
        elif close[i] < close[i - 1]:
            result.append(result[-1] - volume[i])
        else:
            result.append(result[-1])
    return result


# ── Convenience wrapper ───────────────────────────────────────────────────────


def compute_all(
    prices: list[Decimal],
    highs: list[Decimal] | None = None,
    lows: list[Decimal] | None = None,
    volumes: list[Decimal] | None = None,
) -> dict:
    """Compute all indicators and return aligned series (last 100 values).

    highs/lows/volumes are optional; when provided, ATR, Stochastic, VWAP, and OBV
    are also included.
    """
    tail_closes = prices[-200:]
    n = len(tail_closes)

    def _align(series: list) -> list:
        return series[-200:]

    hi = _align(highs) if highs else tail_closes
    lo = _align(lows) if lows else tail_closes
    v = _align(volumes) if volumes else [Decimal("0")] * n

    sma20 = sma(tail_closes, 20)
    sma50 = sma(tail_closes, min(50, n))
    ema12 = ema(tail_closes, 12)
    ema26 = ema(tail_closes, 26)
    rsi14 = rsi(tail_closes, 14)
    macd_data = macd(tail_closes)
    bb = bollinger_bands(tail_closes, 20)
    atr14 = atr(hi, lo, tail_closes, 14)
    stoch = stochastic(hi, lo, tail_closes)
    vwap_series = vwap(hi, lo, tail_closes, v)
    obv_series = obv(tail_closes, v)

    def _tail(series: list, k: int = 100) -> list:
        return [str(v) if v is not None else None for v in series[-k:]]

    k = 100
    return {
        "sma_20": _tail(sma20, k),
        "sma_50": _tail(sma50, k),
        "ema_12": _tail(ema12, k),
        "ema_26": _tail(ema26, k),
        "rsi_14": _tail(rsi14, k),
        "macd_line": _tail(macd_data["macd_line"], k),
        "macd_signal": _tail(macd_data["signal_line"], k),
        "macd_histogram": _tail(macd_data["histogram"], k),
        "bb_upper": _tail(bb["upper"], k),
        "bb_middle": _tail(bb["middle"], k),
        "bb_lower": _tail(bb["lower"], k),
        "atr_14": _tail(atr14, k),
        "stoch_k": _tail(stoch["k"], k),
        "stoch_d": _tail(stoch["d"], k),
        "vwap": _tail(vwap_series, k),
        "obv": _tail(obv_series, k),
    }
