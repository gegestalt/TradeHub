from decimal import ROUND_HALF_UP, Decimal

_MONETARY_DP = Decimal("0.00000001")


def quantize(value: Decimal) -> Decimal:
    return value.quantize(_MONETARY_DP, rounding=ROUND_HALF_UP)


def calculate_fee(price: Decimal, quantity: Decimal, fee_pct: Decimal) -> Decimal:
    return quantize(price * quantity * fee_pct)


def calculate_buy_cost(price: Decimal, quantity: Decimal, fee_pct: Decimal) -> Decimal:
    return quantize(price * quantity + calculate_fee(price, quantity, fee_pct))


def calculate_sell_proceeds(price: Decimal, quantity: Decimal, fee_pct: Decimal) -> Decimal:
    return quantize(price * quantity - calculate_fee(price, quantity, fee_pct))


def calculate_vwac(
    existing_qty: Decimal,
    existing_price: Decimal,
    new_qty: Decimal,
    new_price: Decimal,
) -> Decimal:
    return quantize(
        (existing_price * existing_qty + new_price * new_qty) / (existing_qty + new_qty)
    )
