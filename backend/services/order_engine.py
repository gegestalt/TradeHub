import time
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.ext.asyncio import AsyncSession

import audit
from data_adapters.base import DataAdapter
from services import ledger as ledger_svc
from locks import get_player_lock
from metrics import metrics
from models.competition import Competition
from models.enums import CompetitionState, OrderSide, OrderStatus, OrderType, TimeInForce
from models.order import Order
from models.player import Player
from schemas.order import OCOCreate, OrderCreate
from services.financials import calculate_buy_cost, calculate_fee, calculate_sell_proceeds, quantize
from services.position_manager import apply_position_delta, get_position

_q = quantize


def calculate_vwac(
    existing_qty: Decimal,
    existing_price: Decimal,
    new_qty: Decimal,
    new_price: Decimal,
) -> Decimal:
    from services.financials import calculate_vwac as _vwac
    return _vwac(existing_qty, existing_price, new_qty, new_price)


def should_fill(order: Order, current_price: Decimal) -> bool:
    t = order.order_type
    s = order.side

    if t == OrderType.limit:
        if s == OrderSide.buy:
            return order.limit_price is not None and current_price <= order.limit_price
        return order.limit_price is not None and current_price >= order.limit_price

    if t == OrderType.stop_loss:
        if s == OrderSide.sell:
            return order.stop_price is not None and current_price <= order.stop_price
        return order.stop_price is not None and current_price >= order.stop_price

    if t == OrderType.take_profit:
        if s == OrderSide.sell:
            return order.take_profit_price is not None and current_price >= order.take_profit_price
        return order.take_profit_price is not None and current_price <= order.take_profit_price

    if t == OrderType.stop_limit:
        if s == OrderSide.sell:
            triggered = order.stop_price is not None and current_price <= order.stop_price
            at_limit = order.limit_price is not None and current_price >= order.limit_price
            return triggered and at_limit
        triggered = order.stop_price is not None and current_price >= order.stop_price
        at_limit = order.limit_price is not None and current_price <= order.limit_price
        return triggered and at_limit

    return False


def fill_price_for(order: Order, current_price: Decimal) -> Decimal:
    t = order.order_type
    if t == OrderType.limit:
        return order.limit_price  # type: ignore[return-value]
    if t == OrderType.stop_loss:
        return current_price
    if t == OrderType.take_profit:
        return order.take_profit_price  # type: ignore[return-value]
    if t == OrderType.stop_limit:
        return order.limit_price  # type: ignore[return-value]
    return current_price


async def place_order(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    data: OrderCreate,
    adapter: DataAdapter,
) -> Order:
    t0 = time.perf_counter()
    filled = False
    try:
        from services.market_guardian import get_guardian

        # Block flagged accounts — the reconciler set this when it detected drift.
        if player.trading_halted:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Trading has been halted on your account due to a ledger "
                    "discrepancy. Please contact the competition administrator."
                ),
            )

        ticker = data.ticker.upper()
        _validate_order_context(ticker, competition)

        current_price = adapter.get_price(ticker)

        # Circuit breaker: only meaningful when using a live data adapter.
        # We check the adapter's own source rather than competition.data_source
        # so that service-level tests using MagicMock adapters are never gated.
        from models.enums import DataSource
        if getattr(adapter, "source", None) == DataSource.online:
            guardian = get_guardian(competition.id)
            guardian.record_price(ticker, current_price)
            if guardian.is_paused(ticker):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Trading in {ticker} is temporarily paused due to "
                        f"abnormal price movement. Please try again shortly."
                    ),
                )

        _validate_leverage(data, player, competition, current_price)

        order = _build_order(player.id, data, ticker)
        with db.no_autoflush:
            db.add(order)
        await db.flush()

        if data.order_type == OrderType.market:
            await execute_fill(db, player, competition, order, current_price)

        elif should_fill(order, current_price):
            await execute_fill(db, player, competition, order, fill_price_for(order, current_price))
        elif data.time_in_force in (TimeInForce.ioc, TimeInForce.fok):
            order.status = OrderStatus.expired

        filled = order.status == OrderStatus.filled
        return order
    finally:
        metrics.record_order(time.perf_counter() - t0, filled=filled)


async def place_oco_order(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    data: OCOCreate,
    adapter: DataAdapter,
) -> tuple[Order, Order]:
    ticker = data.ticker.upper()
    _validate_order_context(ticker, competition)

    pair_id = str(uuid.uuid4())
    sl_order = Order(
        player_id=player.id, ticker=ticker, order_type=OrderType.stop_loss,
        side=data.side, quantity=quantize(data.quantity),
        stop_price=data.stop_price, time_in_force=TimeInForce.gtc,
        oco_pair_id=pair_id, status=OrderStatus.pending,
    )
    tp_order = Order(
        player_id=player.id, ticker=ticker, order_type=OrderType.take_profit,
        side=data.side, quantity=quantize(data.quantity),
        take_profit_price=data.take_profit_price, time_in_force=TimeInForce.gtc,
        oco_pair_id=pair_id, status=OrderStatus.pending,
    )
    db.add(sl_order)
    db.add(tp_order)
    await db.flush()
    return sl_order, tp_order


async def cancel_order(db: AsyncSession, player: Player, order_id: str) -> Order:
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.player_id == player.id)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != OrderStatus.pending:
        raise HTTPException(
            status_code=400, detail=f"Cannot cancel an order with status '{order.status}'"
        )

    order.status = OrderStatus.cancelled
    await _cancel_oco_sibling(db, order)
    return order


async def execute_fill(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
) -> None:
    async with get_player_lock(player.id):
        # Refresh inside the lock so we act on the latest committed balance,
        # not the stale snapshot loaded at request start.
        await db.refresh(player)

        with db.no_autoflush:
            balance_before = player.cash_balance
            # Compute fee with the requested quantity first; _execute_buy may
            # lower order.quantity for a partial fill, so we recompute after.
            fee = calculate_fee(price, order.quantity, competition.fee_pct)

            if order.side == OrderSide.buy:
                await _execute_buy(db, player, competition, order, price, fee)
                # Recompute fee against the *actual* filled quantity (may differ
                # from the requested quantity when a partial fill occurred).
                fee = calculate_fee(price, order.quantity, competition.fee_pct)
            else:
                await _execute_sell(db, player, competition, order, price, fee)

            # Always stamp fill metadata — even partial fills need fill_price /
            # fill_at so the order record is complete and queryable.
            if order.status != OrderStatus.cancelled:
                order.fill_price = price
                order.fill_at = datetime.utcnow()
                order.fee_paid = fee
                if order.status != OrderStatus.partial:
                    order.status = OrderStatus.filled

            await audit.log_order_fill(
                db=db,
                player_id=player.id, competition_id=competition.id, order_id=order.id,
                ticker=order.ticker, side=order.side, quantity=order.quantity,
                fill_price=price, fee=fee,
                balance_before=balance_before, balance_after=player.cash_balance,
            )
            # Double-entry ledger — runs inside the same transaction as the
            # balance update so ledger and cash_balance are always consistent.
            if order.side == OrderSide.buy:
                await ledger_svc.record_buy_fill(
                    db, player.id, competition.id, order.id,
                    order.ticker, order.quantity, price, fee,
                )
            else:
                await ledger_svc.record_sell_fill(
                    db, player.id, competition.id, order.id,
                    order.ticker, order.quantity, price, fee,
                )
            await _cancel_oco_sibling(db, order)

    # StaleDataError means another worker/connection modified this player row
    # between our refresh and our UPDATE — the optimistic-lock column (balance_version)
    # caught the conflict. Surface it as a 409 so the client can retry.
    # (In a proper retry loop this would be handled server-side, but for a
    # simulation platform a client-visible 409 is acceptable.)
    try:
        await db.flush()
    except StaleDataError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Balance was modified by a concurrent request. Please retry.",
        ) from exc


async def _execute_buy(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
    fee: Decimal,
) -> None:
    cost = calculate_buy_cost(price, order.quantity, competition.fee_pct)

    if player.cash_balance < cost:
        # Partial fill: buy as many whole shares as the balance allows.
        # For crypto (fractional quantities), scale proportionally.
        if price > 0 and player.cash_balance > Decimal("0"):
            affordable_qty = _max_affordable_qty(
                player.cash_balance, price, competition.fee_pct, order.quantity
            )
        else:
            affordable_qty = Decimal("0")

        if affordable_qty <= Decimal("0"):
            order.status = OrderStatus.cancelled
            await audit.log_order_rejected(
                db=db,
                player_id=player.id, order_id=order.id, ticker=order.ticker,
                reason=f"Insufficient balance: have {player.cash_balance}, need {cost}",
            )
            raise HTTPException(status_code=400, detail="Insufficient cash balance")

        # Partially fill: adjust order quantity and mark as partial.
        original_qty = order.quantity
        order.quantity = affordable_qty
        order.status = OrderStatus.partial
        fee = calculate_fee(price, affordable_qty, competition.fee_pct)
        cost = calculate_buy_cost(price, affordable_qty, competition.fee_pct)
        await audit.log_order_partial_fill(
            db=db,
            player_id=player.id, competition_id=competition.id, order_id=order.id,
            ticker=order.ticker, side=order.side,
            requested_qty=original_qty, filled_qty=affordable_qty,
            fill_price=price, fee=fee,
            balance_before=player.cash_balance,
            balance_after=quantize(player.cash_balance - cost),
        )

    player.cash_balance = quantize(player.cash_balance - cost)

    short_pos = await get_position(db, player.id, order.ticker)
    if short_pos and short_pos.quantity < 0:
        qty_covered = min(order.quantity, abs(short_pos.quantity))
        cover_fee = calculate_fee(price, qty_covered, competition.fee_pct)
        player.realized_pnl = quantize(
            player.realized_pnl + (short_pos.avg_entry_price - price) * qty_covered - cover_fee
        )

    await apply_position_delta(
        db, player, order.ticker, order.quantity, price,
        competition_id=competition.id,
    )


async def _execute_sell(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    order: Order,
    price: Decimal,
    fee: Decimal,
) -> None:
    pos = await get_position(db, player.id, order.ticker)
    available = pos.quantity if pos else Decimal("0")

    if not competition.allow_shorts and available < order.quantity:
        order.status = OrderStatus.cancelled
        await audit.log_order_rejected(
            db=db,
            player_id=player.id, order_id=order.id, ticker=order.ticker,
            reason=f"Insufficient position: have {available}, need {order.quantity}",
        )
        raise HTTPException(status_code=400, detail="Insufficient position quantity to sell")

    proceeds = calculate_sell_proceeds(price, order.quantity, competition.fee_pct)
    player.cash_balance = quantize(player.cash_balance + proceeds)

    if pos and pos.quantity > 0:
        qty_closed = min(order.quantity, pos.quantity)
        cost_basis = pos.avg_entry_price * qty_closed
        gross_revenue = price * qty_closed
        close_fee = calculate_fee(price, qty_closed, competition.fee_pct)
        player.realized_pnl = quantize(
            player.realized_pnl + gross_revenue - cost_basis - close_fee
        )

    await apply_position_delta(
        db, player, order.ticker, -order.quantity, price,
        competition_id=competition.id,
    )


async def _cancel_oco_sibling(db: AsyncSession, order: Order) -> None:
    if not order.oco_pair_id:
        return
    result = await db.execute(
        select(Order).where(
            Order.oco_pair_id == order.oco_pair_id,
            Order.id != order.id,
            Order.status == OrderStatus.pending,
        )
    )
    sibling = result.scalar_one_or_none()
    if sibling:
        sibling.status = OrderStatus.cancelled


def _max_affordable_qty(
    balance: Decimal,
    price: Decimal,
    fee_pct: Decimal,
    requested_qty: Decimal,
) -> Decimal:
    """Return the largest quantity the player can afford given their current balance.

    Always uses ROUND_DOWN (floor) so that qty * cost_per_unit never exceeds
    balance.  The default ROUND_HALF_EVEN can round UP, making the computed
    cost slightly exceed the player's balance and producing a tiny negative.
    """
    cost_per_unit = price * (1 + fee_pct)
    if cost_per_unit <= 0:
        return Decimal("0")
    raw = balance / cost_per_unit
    if price > Decimal("5000") or requested_qty != requested_qty.to_integral_value():
        # Fractional (crypto or high-price asset): floor to 4 decimal places.
        qty = min(raw, requested_qty).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    else:
        # Whole-share assets: floor to the nearest integer.
        qty = Decimal(str(int(min(raw, requested_qty))))
    return max(qty, Decimal("0"))


def _validate_order_context(ticker: str, competition: Competition) -> None:
    if ticker not in [t.upper() for t in competition.asset_universe]:
        raise HTTPException(
            status_code=400,
            detail=f"Ticker '{ticker}' is not in the competition's asset universe",
        )
    if competition.state != CompetitionState.active:
        raise HTTPException(status_code=400, detail="Competition is not active")


def _validate_leverage(
    data: OrderCreate,
    player: Player,
    competition: Competition,
    current_price: Decimal,
) -> None:
    if data.side != OrderSide.buy or competition.max_leverage <= Decimal("0"):
        return
    notional = quantize(current_price * quantize(data.quantity))
    max_notional = quantize(player.cash_balance * competition.max_leverage)
    if notional > max_notional:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order notional {notional} exceeds max leverage "
                f"({competition.max_leverage}x) on balance {player.cash_balance}"
            ),
        )


def _build_order(player_id: str, data: OrderCreate, ticker: str) -> Order:
    # Set all Python-side defaults explicitly so fields are populated immediately
    # on the in-memory object, before the session flushes the INSERT.
    # SQLAlchemy's column `default=` expressions only run at flush time; if code
    # reads order.id / order.fee_paid / order.created_at before the first flush
    # (e.g. to return the order, or in the concurrent-session tests) those fields
    # would be None otherwise.
    return Order(
        id=str(uuid.uuid4()),
        player_id=player_id,
        ticker=ticker,
        order_type=data.order_type,
        side=data.side,
        quantity=quantize(data.quantity),
        limit_price=data.limit_price,
        stop_price=data.stop_price,
        take_profit_price=data.take_profit_price,
        time_in_force=data.time_in_force,
        status=OrderStatus.pending,
        fee_paid=Decimal("0"),
        created_at=datetime.utcnow(),
    )


async def _load_player(db: AsyncSession, player_id: str) -> Player:
    result = await db.execute(select(Player).where(Player.id == player_id))
    return result.scalar_one()
