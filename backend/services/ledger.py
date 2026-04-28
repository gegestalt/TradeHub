"""Double-entry ledger service.

All functions write balanced pairs of LedgerEntry rows (one debit, one credit).
The net of every journal (debit sum == credit sum) is enforced at write time.
A separate invariant checker can verify the global ledger at any time.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ledger_entry import Account, LedgerEntry


def _journal() -> str:
    return str(uuid.uuid4())


async def _write_pair(
    db: AsyncSession,
    journal_id: str,
    player_id: str,
    competition_id: str,
    debit_account: str,
    credit_account: str,
    amount: Decimal,
    order_id: str | None = None,
    ticker: str | None = None,
    description: str | None = None,
) -> None:
    """Write one balanced debit/credit pair to the ledger."""
    from datetime import datetime
    now = datetime.utcnow()
    db.add(LedgerEntry(
        journal_id=journal_id, recorded_at=now,
        player_id=player_id, competition_id=competition_id,
        account_type=debit_account, side="debit", amount=amount,
        order_id=order_id, ticker=ticker, description=description,
    ))
    db.add(LedgerEntry(
        journal_id=journal_id, recorded_at=now,
        player_id=player_id, competition_id=competition_id,
        account_type=credit_account, side="credit", amount=amount,
        order_id=order_id, ticker=ticker, description=description,
    ))


async def record_buy_fill(
    db: AsyncSession,
    player_id: str,
    competition_id: str,
    order_id: str,
    ticker: str,
    quantity: Decimal,
    fill_price: Decimal,
    fee: Decimal,
) -> None:
    """Record a completed buy: cash leaves, position is gained, fee is collected."""
    cost = fill_price * quantity
    jid = _journal()

    # Cash paid for shares
    await _write_pair(
        db, jid, player_id, competition_id,
        debit_account=Account.position(ticker),
        credit_account=Account.CASH_AVAILABLE,
        amount=cost,
        order_id=order_id, ticker=ticker,
        description=f"Buy {quantity} {ticker} @ {fill_price}",
    )

    # Fee charged
    if fee > Decimal("0"):
        await _write_pair(
            db, _journal(), player_id, competition_id,
            debit_account=Account.FEES_COLLECTED,
            credit_account=Account.CASH_AVAILABLE,
            amount=fee,
            order_id=order_id, ticker=ticker,
            description=f"Fee for buy {ticker}",
        )


async def record_sell_fill(
    db: AsyncSession,
    player_id: str,
    competition_id: str,
    order_id: str,
    ticker: str,
    quantity: Decimal,
    fill_price: Decimal,
    fee: Decimal,
) -> None:
    """Record a completed sell: position is liquidated, cash received, fee charged."""
    proceeds = fill_price * quantity
    jid = _journal()

    # Position sold for cash
    await _write_pair(
        db, jid, player_id, competition_id,
        debit_account=Account.CASH_AVAILABLE,
        credit_account=Account.position(ticker),
        amount=proceeds,
        order_id=order_id, ticker=ticker,
        description=f"Sell {quantity} {ticker} @ {fill_price}",
    )

    # Fee charged
    if fee > Decimal("0"):
        await _write_pair(
            db, _journal(), player_id, competition_id,
            debit_account=Account.FEES_COLLECTED,
            credit_account=Account.CASH_AVAILABLE,
            amount=fee,
            order_id=order_id, ticker=ticker,
            description=f"Fee for sell {ticker}",
        )


async def record_starting_balance(
    db: AsyncSession,
    player_id: str,
    competition_id: str,
    amount: Decimal,
) -> None:
    """Record the initial cash allocation when a player joins a competition."""
    await _write_pair(
        db, _journal(), player_id, competition_id,
        debit_account=Account.CASH_AVAILABLE,
        credit_account="COMPETITION_POOL",
        amount=amount,
        description="Starting balance allocation",
    )


async def check_invariant(
    db: AsyncSession,
    player_id: str | None = None,
    competition_id: str | None = None,
) -> dict:
    """Verify that debits == credits for the given scope.

    Returns {"balanced": True/False, "debit_total": ..., "credit_total": ..., "delta": ...}
    A non-zero delta is a critical accounting error — it means funds were
    created or destroyed, which must never happen.
    """
    stmt = select(
        LedgerEntry.side,
        func.sum(LedgerEntry.amount).label("total"),
    ).group_by(LedgerEntry.side)

    if player_id:
        stmt = stmt.where(LedgerEntry.player_id == player_id)
    if competition_id:
        stmt = stmt.where(LedgerEntry.competition_id == competition_id)

    result = await db.execute(stmt)
    rows = {row.side: row.total for row in result}

    debit_total = rows.get("debit", Decimal("0")) or Decimal("0")
    credit_total = rows.get("credit", Decimal("0")) or Decimal("0")
    delta = debit_total - credit_total

    return {
        "balanced": delta == Decimal("0"),
        "debit_total": debit_total,
        "credit_total": credit_total,
        "delta": delta,
    }
