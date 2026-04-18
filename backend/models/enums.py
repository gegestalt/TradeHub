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
    limit = "limit"
    stop_loss = "stop_loss"


class OrderSide(StrEnum):
    buy = "buy"
    sell = "sell"


class OrderStatus(StrEnum):
    pending = "pending"
    filled = "filled"
    cancelled = "cancelled"


class PriceSource(StrEnum):
    online = "online"
    offline = "offline"
