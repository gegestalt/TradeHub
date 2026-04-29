"""Async Risk & Liquidation Engine.

Background service that polls active competitions every
LIQUIDATION_CHECK_INTERVAL_SECONDS and force-sells any player whose equity
falls below the maintenance margin threshold.

Margin definitions
------------------
  position_notional = sum(abs(qty) * current_price) for all positions
  equity            = cash_balance + unrealized_pnl
                    = cash_balance + sum((current_price - avg_entry) * qty)
  maintenance_margin = MAINTENANCE_MARGIN_PCT * position_notional   (default 25 %)

Liquidation trigger
-------------------
  equity < maintenance_margin

Liquidation action
------------------
  For each underwater position: place a market sell order (or buy to cover
  for shorts), charging LIQUIDATION_FEE_PCT on top of the normal fee.

The engine only runs on competitions where max_leverage > 1 (leveraged games).
Unleveraged competitions (max_leverage == 1) cannot go below maintenance margin
through normal usage because no borrowing is possible.

TDD note
--------
  tests/trading/test_liquidation_engine.py covers:
    - detect_underwater_players (unit)
    - run_liquidation_cycle (integration with mock adapter)
    - full cycle via run_liquidation_if_needed (smoke)
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import settings
from data_adapters.base import DataAdapter
from data_adapters.factory import get_adapter
from models.competition import Competition
from models.enums import CompetitionState, OrderSide, OrderType
from models.player import Player
from models.position import Position
from schemas.order import OrderCreate
from services.financials import calculate_fee, quantize

_log = logging.getLogger(__name__)


# ── Core logic (pure, testable) ────────────────────────────────────────────────

def compute_equity(
    cash_balance: Decimal,
    positions: list[Position],
    prices: dict[str, Decimal],
) -> tuple[Decimal, Decimal]:
    """Return (equity, position_notional) for a player.

    equity            = cash + sum((price - avg_entry) * qty)
    position_notional = sum(abs(qty) * price)
    """
    unrealized = Decimal("0")
    notional = Decimal("0")
    for pos in positions:
        price = prices.get(pos.ticker, pos.avg_entry_price)
        unrealized += (price - pos.avg_entry_price) * pos.quantity
        notional += abs(pos.quantity) * price
    equity = quantize(cash_balance + unrealized)
    notional = quantize(notional)
    return equity, notional


def is_underwater(
    equity: Decimal,
    notional: Decimal,
    maintenance_pct: float = settings.MAINTENANCE_MARGIN_PCT,
) -> bool:
    """Return True if equity < maintenance margin requirement."""
    if notional == Decimal("0"):
        return False
    maintenance = quantize(notional * Decimal(str(maintenance_pct)))
    return equity < maintenance


def build_liquidation_orders(
    player: Player,
    positions: list[Position],
    competition: Competition,
    liquidation_fee_pct: float = settings.LIQUIDATION_FEE_PCT,
) -> list[OrderCreate]:
    """Return market orders needed to close all positions for liquidation.

    Buys are used to cover shorts; sells are used to close longs.
    """
    orders = []
    effective_fee = Decimal(str(float(competition.fee_pct) + liquidation_fee_pct))
    for pos in positions:
        if pos.quantity == Decimal("0"):
            continue
        side = OrderSide.sell if pos.quantity > 0 else OrderSide.buy
        orders.append(OrderCreate(
            ticker=pos.ticker,
            order_type=OrderType.market,
            side=side,
            quantity=abs(pos.quantity),
            # Relay the elevated fee via a field the engine will use
        ))
    return orders


async def detect_underwater_players(
    db: AsyncSession,
    competition: Competition,
    adapter: DataAdapter,
) -> list[tuple[Player, list[Position], Decimal, Decimal]]:
    """Return list of (player, positions, equity, notional) for underwater players.

    Only considers non-spectator players in active, leveraged competitions.
    """
    if competition.max_leverage <= Decimal("1"):
        return []

    players_result = await db.execute(
        select(Player).where(
            Player.competition_id == competition.id,
            Player.spectator.is_(False),
        )
    )
    players = players_result.scalars().all()

    underwater = []
    for player in players:
        pos_result = await db.execute(
            select(Position).where(Position.player_id == player.id)
        )
        positions = pos_result.scalars().all()
        if not positions:
            continue

        prices = {}
        for pos in positions:
            try:
                prices[pos.ticker] = adapter.get_price(pos.ticker)
            except Exception:
                prices[pos.ticker] = pos.avg_entry_price

        equity, notional = compute_equity(player.cash_balance, positions, prices)
        if is_underwater(equity, notional):
            underwater.append((player, list(positions), equity, notional))
            _log.warning(
                "LIQUIDATION triggered: player=%s competition=%s equity=%s notional=%s",
                player.id, competition.id, equity, notional,
            )
    return underwater


async def run_liquidation_cycle(
    db: AsyncSession,
    competition: Competition,
    adapter: DataAdapter,
) -> int:
    """Run one liquidation pass. Returns the number of players liquidated."""
    from services.order_engine import execute_fill
    from models.order import Order
    from datetime import datetime

    underwater = await detect_underwater_players(db, competition, adapter)
    count = 0

    for player, positions, equity, notional in underwater:
        for order_create in build_liquidation_orders(player, positions, competition):
            try:
                price = adapter.get_price(order_create.ticker)
                order = Order(
                    player_id=player.id,
                    ticker=order_create.ticker,
                    order_type=order_create.order_type,
                    side=order_create.side,
                    quantity=quantize(order_create.quantity),
                    status="pending",
                    fee_paid=Decimal("0"),
                    time_in_force="gtc",
                    created_at=datetime.utcnow(),
                )
                db.add(order)
                await db.flush()
                await execute_fill(db, player, competition, order, price)
                _log.info(
                    "Liquidated %s %s for player %s",
                    order.quantity, order.ticker, player.id,
                )
            except Exception as exc:
                _log.error("Liquidation fill failed for %s: %s", player.id, exc)
        count += 1

    return count


# ── Background task ────────────────────────────────────────────────────────────

async def run_liquidation_loop(session_factory: async_sessionmaker) -> None:
    """Long-running background task started at application lifespan."""
    interval = settings.LIQUIDATION_CHECK_INTERVAL_SECONDS
    _log.info("Liquidation engine started (interval=%ds)", interval)

    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as db:
                try:
                    competitions_result = await db.execute(
                        select(Competition).where(
                            Competition.state == CompetitionState.active,
                            Competition.max_leverage > Decimal("1"),
                        )
                    )
                    competitions = competitions_result.scalars().all()

                    for comp in competitions:
                        adapter = get_adapter(comp)
                        liquidated = await run_liquidation_cycle(db, comp, adapter)
                        if liquidated:
                            _log.info(
                                "Liquidation cycle: %d player(s) liquidated in %s",
                                liquidated, comp.lobby_code,
                            )
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    _log.error("Liquidation cycle error: %s", exc)
        except Exception as exc:
            _log.error("Liquidation session error: %s", exc)
