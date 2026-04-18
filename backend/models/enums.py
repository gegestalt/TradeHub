from enum import StrEnum


class CompetitionState(StrEnum):
    lobby = "lobby"
    active = "active"
    ended = "ended"


class DataSource(StrEnum):
    online = "online"
    offline = "offline"


class ScoringMethod(StrEnum):
    total_value = "total_value"
    sharpe_ratio = "sharpe_ratio"


class OrderType(StrEnum):
    market = "market"
    limit = "limit"  # fill at limit_price or better
    stop_loss = "stop_loss"  # market execution when stop_price is touched
    take_profit = "take_profit"  # market execution when take_profit_price is touched
    stop_limit = "stop_limit"  # limit execution after stop_price is triggered


class OrderSide(StrEnum):
    buy = "buy"
    sell = "sell"


class OrderStatus(StrEnum):
    pending = "pending"
    filled = "filled"
    cancelled = "cancelled"
    expired = "expired"  # IOC/FOK not immediately fillable


class TimeInForce(StrEnum):
    gtc = "gtc"  # good till cancelled (default)
    ioc = "ioc"  # immediate or cancel
    fok = "fok"  # fill or kill (all or nothing)


class PriceSource(StrEnum):
    online = "online"
    offline = "offline"
