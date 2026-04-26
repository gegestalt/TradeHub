"""Property-based tests for order engine math invariants.

Tests pure calculation functions extracted from order_engine.py so that
Hypothesis can explore the full input space without DB overhead.
"""

from decimal import ROUND_HALF_UP, Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

# ── Mirror of order_engine calculation helpers ────────────────────────────────

_DP8 = Decimal("0.00000001")


def _q(v: Decimal) -> Decimal:
    return v.quantize(_DP8, rounding=ROUND_HALF_UP)


def _fee(price: Decimal, qty: Decimal, fee_pct: Decimal) -> Decimal:
    return _q(price * qty * fee_pct)


def _buy_cost(price: Decimal, qty: Decimal, fee_pct: Decimal) -> Decimal:
    return _q(price * qty + _fee(price, qty, fee_pct))


def _sell_proceeds(price: Decimal, qty: Decimal, fee_pct: Decimal) -> Decimal:
    return _q(price * qty - _fee(price, qty, fee_pct))


def _vwac(qty1: Decimal, price1: Decimal, qty2: Decimal, price2: Decimal) -> Decimal:
    return _q((price1 * qty1 + price2 * qty2) / (qty1 + qty2))


# ── Hypothesis strategies ─────────────────────────────────────────────────────

pos_price = st.decimals(
    min_value="0.01",
    max_value="100000",
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
pos_qty = st.decimals(
    min_value="0.01",
    max_value="10000",
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
fee_pct = st.decimals(
    min_value="0",
    max_value="0.1",
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
big_balance = st.decimals(
    min_value="1000",
    max_value="10000000",
    allow_nan=False,
    allow_infinity=False,
    places=2,
)


# ── Fee invariants ────────────────────────────────────────────────────────────


@given(price=pos_price, qty=pos_qty, pct=fee_pct)
def test_fee_is_non_negative(price, qty, pct):
    assert _fee(price, qty, pct) >= Decimal("0")


@given(price=pos_price, qty=pos_qty, pct=fee_pct)
def test_fee_scales_with_trade_size(price, qty, pct):
    """Double the quantity → double the fee (proportional)."""
    fee1 = _fee(price, qty, pct)
    fee2 = _fee(price, qty * 2, pct)
    assert abs(fee2 - fee1 * 2) <= Decimal("0.00000002")


# ── Buy cost invariants ───────────────────────────────────────────────────────


@given(price=pos_price, qty=pos_qty, pct=fee_pct)
def test_buy_cost_at_least_gross(price, qty, pct):
    assert _buy_cost(price, qty, pct) >= price * qty


@given(price=pos_price, qty=pos_qty)
def test_buy_cost_zero_fee_equals_gross(price, qty):
    gross = _q(price * qty)
    assert _buy_cost(price, qty, Decimal("0")) == gross


# ── Sell proceeds invariants ──────────────────────────────────────────────────


@given(price=pos_price, qty=pos_qty, pct=fee_pct)
def test_sell_proceeds_at_most_gross(price, qty, pct):
    assert _sell_proceeds(price, qty, pct) <= _q(price * qty)


@given(price=pos_price, qty=pos_qty)
def test_sell_proceeds_zero_fee_equals_gross(price, qty):
    gross = _q(price * qty)
    assert _sell_proceeds(price, qty, Decimal("0")) == gross


# ── Round-trip invariants ─────────────────────────────────────────────────────


@given(balance=big_balance, price=pos_price, qty=pos_qty, pct=fee_pct)
def test_buy_sell_round_trip_destroys_money(balance, price, qty, pct):
    """Buying then selling at the same price must not create money."""
    cost = _buy_cost(price, qty, pct)
    assume(cost <= balance)
    after_buy = balance - cost
    after_sell = after_buy + _sell_proceeds(price, qty, pct)
    # Fees destroyed some money; balance cannot exceed the starting amount
    assert after_sell <= balance + Decimal("0.00000002")


@given(price=pos_price, qty=pos_qty)
def test_zero_fee_round_trip_is_neutral(price, qty):
    """With 0% fee, buy cost equals sell proceeds at same price."""
    cost = _buy_cost(price, qty, Decimal("0"))
    proceeds = _sell_proceeds(price, qty, Decimal("0"))
    assert abs(cost - proceeds) <= Decimal("0.00000002")


# ── VWAC invariants ───────────────────────────────────────────────────────────


@given(qty1=pos_qty, price1=pos_price, qty2=pos_qty, price2=pos_price)
def test_vwac_is_between_input_prices(qty1, price1, qty2, price2):
    low, high = min(price1, price2), max(price1, price2)
    result = _vwac(qty1, price1, qty2, price2)
    assert low - Decimal("0.00000001") <= result <= high + Decimal("0.00000001")


@given(price=pos_price, qty1=pos_qty, qty2=pos_qty)
def test_vwac_equal_prices_returns_that_price(price, qty1, qty2):
    result = _vwac(qty1, price, qty2, price)
    assert abs(result - _q(price)) <= Decimal("0.00000002")


@given(qty=pos_qty, price=pos_price)
def test_vwac_symmetric_quantities_is_midpoint(qty, price):
    """VWAC of equal quantities is the arithmetic mean of the two prices."""
    price2 = price * Decimal("2")
    expected = _q((price + price2) / 2)
    result = _vwac(qty, price, qty, price2)
    assert abs(result - expected) <= Decimal("0.00000002")


# ── Rounding invariants ───────────────────────────────────────────────────────


@given(price=pos_price, qty=pos_qty, pct=fee_pct)
def test_all_results_have_at_most_8_decimal_places(price, qty, pct):
    for value in [
        _fee(price, qty, pct),
        _buy_cost(price, qty, pct),
        _sell_proceeds(price, qty, pct),
    ]:  # noqa: E501
        # Quantizing again should not change the value
        assert value == value.quantize(_DP8, rounding=ROUND_HALF_UP)
