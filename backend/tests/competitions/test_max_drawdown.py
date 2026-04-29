"""TDD tests for the Max Drawdown metric in the leaderboard service."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.leaderboard import _compute_max_drawdown


def _snap(total_value: float):
    s = MagicMock()
    s.total_value = Decimal(str(total_value))
    return s


# ── Edge cases ─────────────────────────────────────────────────────────────────

def test_max_drawdown_empty_snapshots():
    assert _compute_max_drawdown([]) == Decimal("0")


def test_max_drawdown_single_snapshot():
    assert _compute_max_drawdown([_snap(10000)]) == Decimal("0")


def test_max_drawdown_monotone_increase():
    """A portfolio that only rises has zero drawdown."""
    snaps = [_snap(v) for v in [10000, 10100, 10200, 10500]]
    assert _compute_max_drawdown(snaps) == Decimal("0")


# ── Basic drawdown scenarios ───────────────────────────────────────────────────

def test_max_drawdown_simple_drop():
    """10 % drop from 10000 to 9000 gives max_drawdown_pct = 10.0000."""
    snaps = [_snap(10000), _snap(9000)]
    result = _compute_max_drawdown(snaps)
    assert result == Decimal("10.0")


def test_max_drawdown_recovery_does_not_erase_past_dd():
    """After a 20 % drop and full recovery, drawdown is still 20 %."""
    snaps = [_snap(v) for v in [10000, 8000, 10000]]
    result = _compute_max_drawdown(snaps)
    assert result == Decimal("20.0")


def test_max_drawdown_takes_largest_drop():
    """Max drawdown is always measured from the global peak.

    Values: [10000, 9000, 9500, 7600]
    Global peak stays at 10000 throughout.
    Worst trough is 7600 → (10000 - 7600) / 10000 = 24 %
    Not 20 % (from local peak 9500), because the algorithm tracks the highest
    value seen so far, not local peaks between drops.
    """
    snaps = [_snap(v) for v in [10000, 9000, 9500, 7600]]
    result = _compute_max_drawdown(snaps)
    assert float(result) == pytest.approx(24.0, abs=0.01)


def test_max_drawdown_final_value_below_start():
    """Portfolio that ends below starting value: drawdown covers the whole fall."""
    snaps = [_snap(v) for v in [10000, 10500, 7000]]
    # Peak = 10500, trough = 7000 → (10500-7000)/10500 = 33.33 %
    result = _compute_max_drawdown(snaps)
    assert float(result) == pytest.approx(33.33, abs=0.1)


def test_max_drawdown_zero_peak_is_handled():
    """Zero peak value must not cause division by zero."""
    snaps = [_snap(0), _snap(0), _snap(100)]
    result = _compute_max_drawdown(snaps)
    assert result >= Decimal("0")


# ── Precision ──────────────────────────────────────────────────────────────────

def test_max_drawdown_precision_to_four_dp():
    """Max drawdown is rounded to 4 decimal places."""
    snaps = [_snap(3), _snap(2)]     # 33.3333...%
    result = _compute_max_drawdown(snaps)
    # 33.3333 rounds to 33.3333 at 4dp
    assert str(result) == "33.3333"


# ── Integration with leaderboard ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_leaderboard_includes_max_drawdown(db):
    """get_leaderboard returns entries with max_drawdown_pct field."""
    from tests.conftest import make_user
    from schemas.competition import CompetitionCreate
    from schemas.order import OrderCreate
    from models.enums import OrderSide
    from services.competition import create_competition, start_competition
    from services.leaderboard import get_leaderboard
    from services.order_engine import place_order
    from unittest.mock import MagicMock

    user, _ = await make_user(db, "DDHost")
    comp, player, _ = await create_competition(
        db,
        CompetitionCreate(
            name="DD Test", starting_balance=Decimal("10000"),
            asset_universe=["AAPL"], fee_pct=Decimal("0"),
        ),
        user,
    )
    await start_competition(db, comp.lobby_code, player)

    adapter = MagicMock()
    adapter.get_price.return_value = Decimal("100")

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        adapter,
    )
    await db.flush()

    entries = await get_leaderboard(db, comp.lobby_code, adapter)
    assert len(entries) == 1
    entry = entries[0]
    assert hasattr(entry, "max_drawdown_pct")
    assert entry.max_drawdown_pct >= Decimal("0")
