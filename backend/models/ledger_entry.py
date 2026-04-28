"""Double-entry ledger.

Every movement of funds in TradeHub is a pair of ledger entries: one debit
and one credit.  The fundamental invariant is:

    SUM of all amounts WHERE side='debit' == SUM of all amounts WHERE side='credit'
    (i.e. the net of the entire ledger is always zero)

This is a stronger guarantee than just checking that no single balance goes
negative — it proves that no funds were created or destroyed.

Account types
-------------
CASH_AVAILABLE   player's spendable balance
CASH_RESERVED    funds locked for pending limit orders (not yet filled)
POSITION         notional value of an open stock/crypto position
FEES_COLLECTED   running total of transaction fees charged

Trade flow (market buy, fee_pct = 0.1%)
----------------------------------------
1. Order placed
   DR CASH_AVAILABLE  1001.00   (cost + fee)
   CR CASH_RESERVED   1001.00

2. Order fills
   DR CASH_RESERVED   1001.00   (release reservation)
   CR CASH_AVAILABLE     0.00   (already debited)
   DR POSITION_AAPL   1000.00   (shares acquired at cost)
   CR CASH_RESERVED   1000.00
   DR FEES_COLLECTED     1.00   (fee to house account)
   CR CASH_RESERVED      1.00

This keeps all accounts balanced to zero net at all times.

NOTE: For the current TradeHub MVP the ledger is written alongside the
existing cash_balance field (dual write).  A future migration can drop
cash_balance and derive it purely from the ledger.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # All entries in a single transaction share the same journal_id
    journal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    player_id: Mapped[str] = mapped_column(String(36), nullable=False)
    competition_id: Mapped[str] = mapped_column(String(36), nullable=False)
    account_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # 'debit'  = funds leaving this account
    # 'credit' = funds entering this account
    side: Mapped[str] = mapped_column(String(6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    # Optional references for traceability
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        Index("ix_ledger_journal_id", "journal_id"),
        Index("ix_ledger_player_id", "player_id"),
        Index("ix_ledger_account_type", "account_type"),
        Index("ix_ledger_recorded_at", "recorded_at"),
    )


# ── Account type constants ─────────────────────────────────────────────────────

class Account:
    CASH_AVAILABLE = "CASH_AVAILABLE"
    CASH_RESERVED = "CASH_RESERVED"
    POSITION = "POSITION"          # suffix with ticker, e.g. "POSITION_AAPL"
    FEES_COLLECTED = "FEES_COLLECTED"

    @staticmethod
    def position(ticker: str) -> str:
        return f"POSITION_{ticker.upper()}"
