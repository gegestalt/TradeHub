"""Nightly ledger reconciler — catches balance drift before it compounds.

Even with double-entry accounting, rounding bugs or edge cases in complex
order flows can cause player.cash_balance to diverge from what the ledger says.
The reconciler catches this by recomputing the expected balance from the
LedgerEntry rows and comparing it to the stored value.

Invariant
---------
    expected_balance = SUM(debit) - SUM(credit)   for account CASH_AVAILABLE
                                                   for this player

This is derived from the ledger semantics:
  - Starting balance → DEBIT CASH_AVAILABLE  (money arrives, increases balance)
  - Buy fill cost    → CREDIT CASH_AVAILABLE (money leaves, decreases balance)
  - Sell proceeds    → DEBIT CASH_AVAILABLE  (money returns, increases balance)
  - Fee              → CREDIT CASH_AVAILABLE (money leaves for fees)
  - Interest         → CREDIT CASH_AVAILABLE (money leaves for borrow cost)

So: expected = sum(all debits for CASH_AVAILABLE) − sum(all credits for CASH_AVAILABLE)

When drift is detected
----------------------
  1. Player.trading_halted is set to True — all subsequent order submissions
     return HTTP 409 until an admin clears the flag.
  2. An AuditLog entry of type "reconciler_flag" is written.
  3. The discrepancy is logged at ERROR level.

Admin endpoint (to clear):
    PATCH /players/{player_id}/admin/resume-trading
    (not implemented here — add when admin panel exists)
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import audit
from config import settings
from models.enums import CompetitionState
from models.ledger_entry import Account, LedgerEntry
from models.player import Player
from models.competition import Competition

_log = logging.getLogger(__name__)

_TOLERANCE = Decimal(settings.RECONCILER_DRIFT_TOLERANCE)


# ── Core reconciliation logic (pure, testable) ─────────────────────────────────

async def compute_expected_balance(db: AsyncSession, player_id: str) -> Decimal:
    """Return the balance implied by the CASH_AVAILABLE ledger entries."""
    result = await db.execute(
        select(
            LedgerEntry.side,
            func.sum(LedgerEntry.amount).label("total"),
        )
        .where(
            LedgerEntry.player_id == player_id,
            LedgerEntry.account_type == Account.CASH_AVAILABLE,
        )
        .group_by(LedgerEntry.side)
    )
    rows = {row.side: row.total or Decimal("0") for row in result}
    debits = rows.get("debit", Decimal("0"))
    credits = rows.get("credit", Decimal("0"))
    return debits - credits


async def reconcile_player(
    db: AsyncSession,
    player: Player,
) -> dict:
    """Check one player's cash_balance against their ledger.

    Returns a dict with:
      - expected: Decimal  computed from ledger
      - actual:   Decimal  stored in Player.cash_balance
      - drift:    Decimal  actual − expected (negative = balance too low)
      - flagged:  bool     True if drift exceeded tolerance and account was halted
    """
    expected = await compute_expected_balance(db, player.id)
    actual = player.cash_balance
    drift = actual - expected

    flagged = False
    if abs(drift) > _TOLERANCE:
        _log.error(
            "LEDGER DRIFT DETECTED player=%s  expected=%s  actual=%s  drift=%s",
            player.id, expected, actual, drift,
        )
        player.trading_halted = True
        flagged = True
        await audit.log_order_rejected(
            db=db,
            player_id=player.id,
            order_id="reconciler",
            ticker="N/A",
            reason=(
                f"Reconciler flagged account: "
                f"ledger_expected={expected} cash_balance={actual} drift={drift}"
            ),
        )

    return {
        "player_id": player.id,
        "expected": expected,
        "actual": actual,
        "drift": drift,
        "flagged": flagged,
    }


async def run_reconciliation(db: AsyncSession) -> list[dict]:
    """Reconcile all players in active competitions. Returns per-player reports."""
    competitions_result = await db.execute(
        select(Competition).where(Competition.state == CompetitionState.active)
    )
    competitions = competitions_result.scalars().all()

    reports = []
    for comp in competitions:
        players_result = await db.execute(
            select(Player).where(
                Player.competition_id == comp.id,
                Player.spectator.is_(False),
            )
        )
        for player in players_result.scalars().all():
            report = await reconcile_player(db, player)
            reports.append(report)
            if report["flagged"]:
                _log.warning(
                    "Trading halted for player %s in competition %s (drift=%s)",
                    player.id, comp.lobby_code, report["drift"],
                )

    return reports


# ── Background loop ────────────────────────────────────────────────────────────

async def run_reconciler_loop(session_factory: async_sessionmaker) -> None:
    """Long-running background task. Reconciles once per RECONCILER_INTERVAL_SECONDS."""
    interval = settings.RECONCILER_INTERVAL_SECONDS
    _log.info("Reconciler started (interval=%ds, tolerance=%s)", interval, _TOLERANCE)

    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as db:
                try:
                    reports = await run_reconciliation(db)
                    flagged = sum(1 for r in reports if r["flagged"])
                    clean = len(reports) - flagged
                    if reports:
                        _log.info(
                            "Reconciliation complete: %d clean, %d flagged",
                            clean, flagged,
                        )
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    _log.error("Reconciliation error: %s", exc)
        except Exception as exc:
            _log.error("Reconciler session error: %s", exc)
