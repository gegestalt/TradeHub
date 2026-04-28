"""Tests for the in-memory TTL price cache."""

import time
from decimal import Decimal

import pytest

from services.cache import _InMemoryCache as _PriceCache


@pytest.fixture
def cache():
    return _PriceCache(ttl_seconds=1)


def test_miss_on_empty(cache):
    assert cache.get("AAPL", "online") is None


def test_set_and_hit(cache):
    cache.set("AAPL", "online", Decimal("150.00"))
    assert cache.get("AAPL", "online") == Decimal("150.00")


def test_different_sources_are_independent(cache):
    cache.set("AAPL", "online", Decimal("150"))
    cache.set("AAPL", "offline", Decimal("140"))
    assert cache.get("AAPL", "online") == Decimal("150")
    assert cache.get("AAPL", "offline") == Decimal("140")


def test_different_tickers_are_independent(cache):
    cache.set("AAPL", "online", Decimal("150"))
    cache.set("GOOG", "online", Decimal("200"))
    assert cache.get("AAPL", "online") == Decimal("150")
    assert cache.get("GOOG", "online") == Decimal("200")


def test_expired_entry_returns_none():
    short_cache = _PriceCache(ttl_seconds=0)
    short_cache.set("AAPL", "online", Decimal("150"))
    time.sleep(0.01)
    assert short_cache.get("AAPL", "online") is None


def test_delete_removes_entry(cache):
    cache.set("AAPL", "online", Decimal("150"))
    cache.delete("AAPL", "online")
    assert cache.get("AAPL", "online") is None


def test_delete_nonexistent_is_noop(cache):
    cache.delete("AAPL", "online")  # should not raise


def test_size(cache):
    assert cache.size == 0
    cache.set("AAPL", "online", Decimal("150"))
    cache.set("GOOG", "online", Decimal("200"))
    assert cache.size == 2


def test_clear(cache):
    cache.set("AAPL", "online", Decimal("150"))
    cache.clear()
    assert cache.size == 0
    assert cache.get("AAPL", "online") is None


def test_overwrite_refreshes_value(cache):
    cache.set("AAPL", "online", Decimal("150"))
    cache.set("AAPL", "online", Decimal("200"))
    assert cache.get("AAPL", "online") == Decimal("200")
