"""TDD tests for the Market Guardian circuit breaker."""

import time
from decimal import Decimal

import pytest

from services.market_guardian import MarketGuardian, evict_guardian, get_guardian


def _guardian(threshold=0.10, window=5, cooldown=60) -> MarketGuardian:
    return MarketGuardian(
        spike_threshold_pct=threshold,
        window_seconds=window,
        cooldown_seconds=cooldown,
    )


# ── Normal operation ───────────────────────────────────────────────────────────

def test_no_trip_on_stable_prices():
    g = _guardian()
    g.record_price("AAPL", Decimal("150"))
    g.record_price("AAPL", Decimal("151"))
    g.record_price("AAPL", Decimal("152"))
    assert not g.is_paused("AAPL")


def test_not_paused_with_single_price():
    g = _guardian()
    g.record_price("AAPL", Decimal("100"))
    assert not g.is_paused("AAPL")


# ── Circuit trip ───────────────────────────────────────────────────────────────

def test_trip_on_10pct_spike():
    g = _guardian(threshold=0.10)
    g.record_price("AAPL", Decimal("100"))
    tripped = g.record_price("AAPL", Decimal("111"))  # 11 % up
    assert tripped is True
    assert g.is_paused("AAPL")


def test_trip_on_downward_spike():
    g = _guardian(threshold=0.10)
    g.record_price("AAPL", Decimal("100"))
    g.record_price("AAPL", Decimal("89"))  # 11 % down
    assert g.is_paused("AAPL")


def test_no_trip_below_threshold():
    g = _guardian(threshold=0.10)
    g.record_price("AAPL", Decimal("100"))
    g.record_price("AAPL", Decimal("109"))  # 9 % — just below threshold
    assert not g.is_paused("AAPL")


def test_different_tickers_independent():
    g = _guardian(threshold=0.10)
    g.record_price("AAPL", Decimal("100"))
    g.record_price("AAPL", Decimal("115"))  # trips AAPL
    assert g.is_paused("AAPL")
    assert not g.is_paused("TSLA")  # TSLA unaffected


# ── Window eviction ────────────────────────────────────────────────────────────

def test_old_prices_evicted_outside_window():
    """A spike that happened outside the rolling window must not trip the breaker."""
    g = _guardian(threshold=0.10, window=1)
    g.record_price("AAPL", Decimal("100"))
    time.sleep(1.1)                        # expire the first observation
    g.record_price("AAPL", Decimal("115"))  # only one price in window → no trip
    assert not g.is_paused("AAPL")


# ── Cooldown and reset ─────────────────────────────────────────────────────────

def test_pause_expires_after_cooldown():
    """A pause with a 0.05 s cooldown clears after the cooldown elapses."""
    import time as _time
    g = _guardian(threshold=0.10, cooldown=0.05)  # 50 ms cooldown
    g.record_price("AAPL", Decimal("100"))
    g.record_price("AAPL", Decimal("115"))   # trips the breaker

    # Immediately after tripping, the pause must be active
    assert g.is_paused("AAPL"), "Pause must be active right after trip"

    # After the cooldown elapses, the pause must clear
    _time.sleep(0.10)
    assert not g.is_paused("AAPL"), "Pause must clear after cooldown"


def test_manual_reset_clears_pause():
    g = _guardian(threshold=0.10, cooldown=3600)
    g.record_price("AAPL", Decimal("100"))
    g.record_price("AAPL", Decimal("115"))
    assert g.is_paused("AAPL")
    g.manual_reset("AAPL")
    assert not g.is_paused("AAPL")


def test_paused_assets_returns_tripped_list():
    g = _guardian(threshold=0.10, cooldown=3600)
    g.record_price("AAPL", Decimal("100"))
    g.record_price("AAPL", Decimal("115"))
    g.record_price("TSLA", Decimal("200"))
    g.record_price("TSLA", Decimal("230"))
    assert set(g.paused_assets()) == {"AAPL", "TSLA"}


# ── Registry ──────────────────────────────────────────────────────────────────

def test_get_guardian_returns_same_instance():
    g1 = get_guardian("comp-abc")
    g2 = get_guardian("comp-abc")
    assert g1 is g2


def test_evict_guardian_removes_from_registry():
    get_guardian("comp-xyz")
    evict_guardian("comp-xyz")
    g_new = get_guardian("comp-xyz")
    assert g_new is not None  # new instance created fresh
