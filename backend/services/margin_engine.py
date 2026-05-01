"""Margin & Interest Engine.

Charges a daily borrow rate on the leveraged portion of a player's position.
Runs as a background task every MARGIN_INTEREST_INTERVAL_SECONDS (default 24 h).

Interest calculation
--------------------
    borrowed_notional = max(0, position_notional − cash_balance)
    daily_interest    = borrowed_notional × MARGIN_DAILY_BORROW_RATE
    (default rate: 0.02 % / day ≈ 7.3 % / year)

The interest is:
  1. Debited from player.cash_balance.
  2. Recorded as a double-entry LedgerEntry pair (FEES_COLLECTED / CASH_AVAILABLE).
  3. Written to AuditLog for compliance.

Only leveraged competitions (max_leverage > 1) are affected.
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import audit
from config import settings
from models.competition import Competition
from models.enums import CompetitionState
from models.player import Player
from models.position import Position
from services.financials import quantize
from services.ledger import record_sell_fill  # re-use the fee-pair writer

_log = logging.getLogger(__name__)

_DAILY_RATE = Decimal(str(settings.MARGIN_DAILY_BORROW_RATE))


# ── Core logic ─────────────────────────────────────────────────────────────────

def compute_interest(
    cash_balance: Decimal,
    positions: list[Position],
    prices: dict[str, Decimal],
    daily_rate: Decimal = _DAILY_RATE,
) -> Decimal:
    """Return the interest charge for one day (or one interval).

    borrowed_notional = max(0, position_notional - cash_balance)
    interest = borrowed_notional * daily_rate
    """
    notional = sum(abs(pos.quantity) * prices.get(pos.ticker, pos.avg_entry_price)
                   for pos in positions)
    borrowed = max(Decimal("0"), notional - cash_balance)
    return quantize(borrowed * daily_rate)


async def charge_interest(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    interest: Decimal,
) -> None:
    """Debit interest from player balance and write ledger + audit entries."""
    if interest <= Decimal("0"):
        return

    player.cash_balance = quantize(player.cash_balance - interest)

    # Ledger: FEES_COLLECTED debit (interest expense) / CASH_AVAILABLE credit
    from services.ledger import _write_pair, _journal
    await _write_pair(
        db,
        journal_id=_journal(),
        player_id=player.id,
        competition_id=competition.id,
        debit_account="INTEREST_EXPENSE",
        credit_account="CASH_AVAILABLE",
        amount=interest,
        description=f"Daily margin borrow cost @ {float(_DAILY_RATE):.4%}/day",
    )

    await audit.log_order_rejected(
        db=db,
        player_id=player.id,
        order_id="margin_interest",
        ticker="N/A",
        reason=f"Daily margin interest charged: {interest} (rate={_DAILY_RATE})",
    )
    _log.debug(
        "Margin interest %s charged to player %s (competition %s)",
        interest, player.id, competition.id,
    )


async def run_interest_cycle(
    db: AsyncSession,
    competition: Competition,
    adapter,
) -> int:
    """Charge interest for all leveraged players in one competition.

    Returns the number of players charged.
    """
    if competition.max_leverage <= Decimal("1"):
        return 0

    players_result = await db.execute(
        select(Player).where(
            Player.competition_id == competition.id,
            Player.spectator.is_(False),
        )
    )
    charged = 0
    for player in players_result.scalars().all():
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

        interest = compute_interest(player.cash_balance, positions, prices)
        if interest > Decimal("0"):
            await charge_interest(db, player, competition, interest)
            charged += 1

    return charged


# ── Background loop ────────────────────────────────────────────────────────────

async def run_margin_interest_loop(session_factory: async_sessionmaker) -> None:
    """Long-running background task — charges interest once per interval."""
    from data_adapters.factory import get_adapter
    interval = settings.MARGIN_INTEREST_INTERVAL_SECONDS
    _log.info("Margin interest engine started (interval=%ds, rate=%s/day)",
              interval, _DAILY_RATE)

    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as db:
                try:
                    comps_result = await db.execute(
                        select(Competition).where(
                            Competition.state == CompetitionState.active,
                            Competition.max_leverage > Decimal("1"),
                        )
                    )
                    total_charged = 0
                    for comp in comps_result.scalars().all():
                        adapter = get_adapter(comp)
                        n = await run_interest_cycle(db, comp, adapter)
                        total_charged += n
                    if total_charged:
                        _log.info("Margin interest cycle: charged %d players", total_charged)
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    _log.error("Margin interest cycle error: %s", exc)
        except Exception as exc:
            _log.error("Margin interest session error: %s", exc)
