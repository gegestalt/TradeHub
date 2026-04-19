"""Tests for technical indicator calculations."""

from decimal import Decimal

from services.indicators import bollinger_bands, compute_all, ema, macd, rsi, sma


def d(v) -> Decimal:
    return Decimal(str(v))


PRICES = [d(x) for x in range(1, 51)]  # 1..50


class TestSMA:
    def test_length_matches_input(self):
        result = sma(PRICES, 5)
        assert len(result) == len(PRICES)

    def test_first_values_are_none(self):
        result = sma(PRICES, 5)
        assert result[:4] == [None] * 4

    def test_first_valid_value(self):
        result = sma(PRICES, 5)
        assert result[4] == d("3.00000")  # (1+2+3+4+5)/5

    def test_period_equals_length(self):
        result = sma(PRICES[:3], 3)
        assert result[2] == d("2.00000")

    def test_shorter_than_period_all_none(self):
        result = sma(PRICES[:3], 10)
        assert all(v is None for v in result)


class TestEMA:
    def test_length_matches_input(self):
        assert len(ema(PRICES, 5)) == len(PRICES)

    def test_first_values_none(self):
        result = ema(PRICES, 5)
        assert result[:4] == [None] * 4

    def test_seed_equals_sma(self):
        result = ema(PRICES, 5)
        assert result[4] == d("3.00000")

    def test_subsequent_value_higher_for_rising_prices(self):
        result = ema(PRICES, 5)
        assert result[5] > result[4]  # prices are rising so EMA grows


class TestRSI:
    def test_length_matches(self):
        assert len(rsi(PRICES, 14)) == len(PRICES)

    def test_first_values_none(self):
        result = rsi(PRICES, 14)
        assert result[:14] == [None] * 14

    def test_all_gains_gives_rsi_100(self):
        # Uniformly rising prices → avg_loss = 0 → RSI = 100
        result = rsi(PRICES, 14)
        assert result[14] == d("100")

    def test_insufficient_data(self):
        assert all(v is None for v in rsi([d(1), d(2)], 14))


class TestMACD:
    def test_keys_present(self):
        result = macd(PRICES)
        assert "macd_line" in result
        assert "signal_line" in result
        assert "histogram" in result

    def test_lengths_equal(self):
        result = macd(PRICES)
        assert len(result["macd_line"]) == len(result["signal_line"]) == len(result["histogram"])

    def test_macd_positive_for_rising(self):
        # For rising prices, fast EMA > slow EMA → positive MACD
        long_prices = [d(x) for x in range(1, 101)]
        result = macd(long_prices)
        non_none = [v for v in result["macd_line"] if v is not None]
        assert all(v > 0 for v in non_none)


class TestBollingerBands:
    def test_keys_present(self):
        result = bollinger_bands(PRICES, 20)
        assert "upper" in result and "middle" in result and "lower" in result

    def test_upper_above_lower(self):
        result = bollinger_bands(PRICES, 5)
        for upper, lower in zip(result["upper"], result["lower"], strict=True):
            if upper is not None and lower is not None:
                assert upper >= lower

    def test_middle_between_bands(self):
        result = bollinger_bands(PRICES, 5)
        for upper, mid, lower in zip(
            result["upper"], result["middle"], result["lower"], strict=True
        ):
            if upper is not None:
                assert lower <= mid <= upper


class TestComputeAll:
    def test_returns_all_keys(self):
        long_prices = [d(x) for x in range(1, 201)]
        result = compute_all(long_prices)
        expected_keys = {
            "sma_20", "sma_50", "ema_12", "ema_26",
            "rsi_14", "macd_line", "macd_signal", "macd_histogram",
            "bb_upper", "bb_middle", "bb_lower",
            "atr_14", "stoch_k", "stoch_d", "vwap", "obv",
        }
        assert expected_keys == set(result.keys())

    def test_output_length_capped_at_100(self):
        long_prices = [d(x) for x in range(1, 201)]
        result = compute_all(long_prices)
        for series in result.values():
            assert len(series) == 100

    def test_values_are_strings_or_none(self):
        long_prices = [d(x) for x in range(1, 201)]
        result = compute_all(long_prices)
        for series in result.values():
            for v in series:
                assert v is None or isinstance(v, str)
