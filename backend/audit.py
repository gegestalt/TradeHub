import json
import logging
from datetime import datetime
from decimal import Decimal

_logger = logging.getLogger("tradehub.audit")


def _emit(**kwargs) -> None:
    _logger.info(json.dumps({"ts": datetime.utcnow().isoformat(), **kwargs}, default=str))


def log_order_fill(
    *,
    player_id: str,
    competition_id: str,
    order_id: str,
    ticker: str,
    side: str,
    quantity: Decimal,
    fill_price: Decimal,
    fee: Decimal,
    balance_before: Decimal,
    balance_after: Decimal,
) -> None:
    _emit(
        event="order_fill",
        player_id=player_id,
        competition_id=competition_id,
        order_id=order_id,
        ticker=ticker,
        side=side,
        quantity=quantity,
        fill_price=fill_price,
        fee=fee,
        balance_before=balance_before,
        balance_after=balance_after,
    )


def log_order_rejected(*, player_id: str, order_id: str, ticker: str, reason: str) -> None:
    _emit(
        event="order_rejected",
        player_id=player_id,
        order_id=order_id,
        ticker=ticker,
        reason=reason,
    )


def log_position_change(
    *,
    player_id: str,
    ticker: str,
    qty_before: Decimal,
    qty_after: Decimal,
    avg_entry_price: Decimal,
) -> None:
    _emit(
        event="position_change",
        player_id=player_id,
        ticker=ticker,
        qty_before=qty_before,
        qty_after=qty_after,
        avg_entry_price=avg_entry_price,
    )
