from enum import Enum


class CompetitionState(str, Enum):
    lobby = "lobby"
    active = "active"
    ended = "ended"


class DataSource(str, Enum):
    online = "online"
    offline = "offline"


class ScoringMethod(str, Enum):
    total_value = "total_value"
    sharpe_ratio = "sharpe_ratio"


class OrderType(str, Enum):
    market = "market"
    limit = "limit"
    stop_loss = "stop_loss"


class OrderSide(str, Enum):
    buy = "buy"
    sell = "sell"


class OrderStatus(str, Enum):
    pending = "pending"
    filled = "filled"
    cancelled = "cancelled"


class PriceSource(str, Enum):
    online = "online"
    offline = "offline"
