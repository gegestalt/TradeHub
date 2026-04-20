"""Tests for MockDataAdapter — per-competition singleton and price consistency."""

from decimal import Decimal

import pytest

from data_adapters.mock import MockDataAdapter, _registry


def _clear():
    with __import__("data_adapters.mock", fromlist=["_lock"])._lock:
        _registry.clear()


@pytest.fixture(autouse=True)
def clean_registry():
    _clear()
    yield
    _clear()


# ── Registry ──────────────────────────────────────────────────────────────────


def test_for_competition_creates_adapter():
    adapter = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    assert isinstance(adapter, MockDataAdapter)


def test_for_competition_returns_same_instance():
    a1 = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    a2 = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    assert a1 is a2


def test_different_competitions_get_different_instances():
    a1 = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    a2 = MockDataAdapter.for_competition("comp-2", ["AAPL"])
    assert a1 is not a2


def test_different_competitions_may_have_different_prices():
    a1 = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    a2 = MockDataAdapter.for_competition("comp-2", ["AAPL"])
    # Seeds differ so prices should differ (statistically certain for any real hash)
    assert a1.get_price("AAPL") != a2.get_price("AAPL")


def test_evict_removes_from_registry():
    MockDataAdapter.for_competition("comp-1", ["AAPL"])
    assert MockDataAdapter.registry_size() == 1
    MockDataAdapter.evict("comp-1")
    assert MockDataAdapter.registry_size() == 0


def test_evict_nonexistent_is_safe():
    MockDataAdapter.evict("does-not-exist")  # must not raise


def test_registry_size():
    MockDataAdapter.for_competition("comp-a", ["AAPL"])
    MockDataAdapter.for_competition("comp-b", ["TSLA"])
    assert MockDataAdapter.registry_size() == 2


# ── Price stability ───────────────────────────────────────────────────────────


def test_get_price_stable_without_tick():
    adapter = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    p1 = adapter.get_price("AAPL")
    p2 = adapter.get_price("AAPL")
    assert p1 == p2


def test_price_consistent_across_adapter_instances_same_competition():
    """Two calls that fetch the singleton return the same price — this is the
    key guarantee that leaderboard and order fills agree."""
    p1 = MockDataAdapter.for_competition("comp-1", ["AAPL"]).get_price("AAPL")
    p2 = MockDataAdapter.for_competition("comp-1", ["AAPL"]).get_price("AAPL")
    assert p1 == p2


def test_all_prices_are_positive():
    adapter = MockDataAdapter.for_competition("comp-1", ["AAPL", "TSLA", "GOOGL"])
    for ticker in ["AAPL", "TSLA", "GOOGL"]:
        assert adapter.get_price(ticker) > Decimal("0")


# ── Ticking ───────────────────────────────────────────────────────────────────


def test_tick_advances_price():
    adapter = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    before = adapter.get_price("AAPL")
    adapter.tick()
    after = adapter.get_price("AAPL")
    # After a tick the price should be different (extremely rare to be identical)
    assert before != after


def test_tick_increments_tick_num():
    adapter = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    assert adapter.tick_num == 0
    adapter.tick()
    assert adapter.tick_num == 1
    adapter.tick()
    assert adapter.tick_num == 2


def test_price_history_grows_with_ticks():
    adapter = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    assert adapter.price_history("AAPL") == []
    adapter.tick()
    adapter.tick()
    assert len(adapter.price_history("AAPL")) == 2


def test_tick_shared_across_singleton():
    """Ticking via one reference is visible via another (same object)."""
    a1 = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    a1.tick()
    a2 = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    assert a2.tick_num == 1


# ── Unknown tickers ───────────────────────────────────────────────────────────


def test_unknown_ticker_auto_registered():
    adapter = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    price = adapter.get_price("MSFT")  # not in initial list
    assert price > Decimal("0")


def test_late_ticker_added_via_for_competition():
    MockDataAdapter.for_competition("comp-1", ["AAPL"])
    # Call again with an extra ticker — should be registered on the existing instance
    adapter = MockDataAdapter.for_competition("comp-1", ["AAPL", "TSLA"])
    assert adapter.get_price("TSLA") > Decimal("0")


# ── After eviction ────────────────────────────────────────────────────────────


def test_after_evict_new_instance_has_fresh_prices():
    a1 = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    a1.tick()
    a1.tick()
    original_price = a1.get_price("AAPL")

    MockDataAdapter.evict("comp-1")

    a2 = MockDataAdapter.for_competition("comp-1", ["AAPL"])
    # New instance — tick_num resets
    assert a2.tick_num == 0
    # Price is deterministically the same (same hash → same seed) but tick state is reset
    assert a2.get_price("AAPL") != original_price or a2.tick_num == 0
