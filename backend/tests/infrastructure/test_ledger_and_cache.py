"""Tests for the double-entry ledger and the Redis-ready price cache.

Ledger tests verify:
  - Every fill writes balanced debit/credit pairs
  - The invariant check detects any unbalanced state
  - buy / sell / partial flows all produce correct account movements

Cache tests verify:
  - In-memory cache TTL, hit/miss, clear behaviour
  - Redis backend contract via a mock (no live Redis required)
  - Fallback to in-memory when Redis is unavailable
"""

import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from models.ledger_entry import Account, LedgerEntry
from models.enums import OrderSide
from schemas.competition import CompetitionCreate
from schemas.order import OrderCreate
from services.cache import _InMemoryCache, _RedisCache
from services.competition import create_competition, start_competition
from services.ledger import (
    check_invariant,
    record_buy_fill,
    record_sell_fill,
    record_starting_balance,
)
from services.order_engine import place_order
from tests.conftest import make_user


# ═══════════════════════════════════════════════════════════════════════════════
# LEDGER TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def _adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(db, balance: Decimal = Decimal("10000"), fee: Decimal = Decimal("0")):
    user, _ = await make_user(db, "Host")
    comp, player, _ = await create_competition(
        db,
        CompetitionCreate(
            name="Ledger Test", starting_balance=balance,
            asset_universe=["AAPL", "TSLA"], fee_pct=fee,
        ),
        user,
    )
    await start_competition(db, comp.lobby_code, player)
    return comp, player


# ── Invariant: debits always equal credits ─────────────────────────────────────

@pytest.mark.asyncio
async def test_ledger_invariant_holds_on_buy(db):
    """After a buy fill, total debits == total credits."""
    comp, player = await _setup(db)
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        _adapter(Decimal("100")),
    )
    await db.flush()

    result = await check_invariant(db, player_id=player.id)
    assert result["balanced"], (
        f"Ledger imbalanced after buy: delta={result['delta']}"
    )
    assert result["delta"] == Decimal("0")


@pytest.mark.asyncio
async def test_ledger_invariant_holds_on_buy_and_sell(db):
    """After buy then sell, invariant still holds."""
    comp, player = await _setup(db)
    adapter = _adapter(Decimal("100"))

    await place_order(db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")), adapter)
    await place_order(db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("3")), adapter)
    await db.flush()

    result = await check_invariant(db, player_id=player.id)
    assert result["balanced"], f"Ledger imbalanced: delta={result['delta']}"


@pytest.mark.asyncio
async def test_ledger_invariant_holds_with_fees(db):
    """Fees are part of the ledger and still keep it balanced."""
    comp, player = await _setup(db, fee=Decimal("0.001"))
    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        _adapter(Decimal("100")),
    )
    await db.flush()

    result = await check_invariant(db, player_id=player.id)
    assert result["balanced"]


@pytest.mark.asyncio
async def test_ledger_invariant_detects_corruption(db):
    """Manually inserting an orphan debit entry breaks the invariant."""
    comp, player = await _setup(db)

    # Manually insert ONE debit without a matching credit (corruption simulation)
    from models.ledger_entry import LedgerEntry
    from datetime import datetime
    db.add(LedgerEntry(
        journal_id="bad-journal",
        recorded_at=datetime.utcnow(),
        player_id=player.id,
        competition_id=comp.id,
        account_type=Account.CASH_AVAILABLE,
        side="debit",
        amount=Decimal("42"),
        description="Corrupted entry — no matching credit",
    ))
    await db.flush()

    result = await check_invariant(db, player_id=player.id)
    assert not result["balanced"]
    assert result["delta"] == Decimal("42")


# ── Account movements ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buy_fill_creates_position_and_cash_entries(db):
    """record_buy_fill creates a POSITION debit and CASH_AVAILABLE credit."""
    comp, player = await _setup(db)
    await record_buy_fill(
        db, player.id, comp.id, "order-001",
        "AAPL", Decimal("5"), Decimal("100"), Decimal("0"),
    )
    await db.flush()

    rows = (await db.execute(select(LedgerEntry).where(
        LedgerEntry.player_id == player.id,
    ))).scalars().all()

    # Should have exactly 2 entries: POSITION debit + CASH_AVAILABLE credit
    assert len(rows) == 2
    accounts = {r.account_type for r in rows}
    assert Account.position("AAPL") in accounts
    assert Account.CASH_AVAILABLE in accounts

    debit = next(r for r in rows if r.side == "debit")
    credit = next(r for r in rows if r.side == "credit")
    assert debit.amount == credit.amount == Decimal("500")  # 5 * 100


@pytest.mark.asyncio
async def test_buy_with_fee_creates_three_entries(db):
    """A buy with non-zero fee writes 4 entries (2 pairs): cost + fee."""
    comp, player = await _setup(db)
    await record_buy_fill(
        db, player.id, comp.id, "order-002",
        "AAPL", Decimal("1"), Decimal("100"), Decimal("0.10"),
    )
    await db.flush()

    rows = (await db.execute(select(LedgerEntry))).scalars().all()
    assert len(rows) == 4  # 2 pairs: cost pair + fee pair


@pytest.mark.asyncio
async def test_sell_fill_creates_cash_and_position_entries(db):
    """record_sell_fill creates a CASH_AVAILABLE debit and POSITION credit."""
    comp, player = await _setup(db)
    await record_sell_fill(
        db, player.id, comp.id, "order-003",
        "AAPL", Decimal("3"), Decimal("110"), Decimal("0"),
    )
    await db.flush()

    rows = (await db.execute(select(LedgerEntry))).scalars().all()
    assert len(rows) == 2
    accounts = {r.account_type for r in rows}
    assert Account.CASH_AVAILABLE in accounts
    assert Account.position("AAPL") in accounts


@pytest.mark.asyncio
async def test_starting_balance_entry(db):
    """record_starting_balance creates a CASH_AVAILABLE debit (player receives funds)."""
    comp, player = await _setup(db)
    await record_starting_balance(db, player.id, comp.id, Decimal("10000"))
    await db.flush()

    rows = (await db.execute(select(LedgerEntry).where(
        LedgerEntry.account_type == Account.CASH_AVAILABLE,
        LedgerEntry.side == "debit",
    ))).scalars().all()

    assert len(rows) >= 1
    assert any(r.amount == Decimal("10000") for r in rows)


@pytest.mark.asyncio
async def test_ledger_tied_to_fill_via_order_engine(db):
    """Placing an order via the engine writes ledger entries alongside the fill."""
    comp, player = await _setup(db, balance=Decimal("5000"))
    await place_order(
        db, player, comp,
        OrderCreate(ticker="TSLA", side=OrderSide.buy, quantity=Decimal("10")),
        _adapter(Decimal("200")),
    )
    await db.flush()

    rows = (await db.execute(select(LedgerEntry))).scalars().all()
    assert len(rows) >= 2, "At least one debit/credit pair expected"

    # The POSITION_TSLA debit should equal qty * price = 2000
    pos_entries = [r for r in rows if r.account_type == Account.position("TSLA")]
    assert any(r.amount == Decimal("2000") for r in pos_entries)


# ═══════════════════════════════════════════════════════════════════════════════
# CACHE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

# ── In-memory cache ────────────────────────────────────────────────────────────

def test_memory_cache_hit_within_ttl():
    c = _InMemoryCache(ttl_seconds=60)
    c.set("AAPL", "online", Decimal("150"))
    assert c.get("AAPL", "online") == Decimal("150")


def test_memory_cache_miss_after_ttl():
    c = _InMemoryCache(ttl_seconds=0)  # expires immediately
    c.set("AAPL", "online", Decimal("150"))
    time.sleep(0.01)
    assert c.get("AAPL", "online") is None


def test_memory_cache_miss_on_unknown_ticker():
    c = _InMemoryCache(ttl_seconds=60)
    assert c.get("UNKNOWN", "online") is None


def test_memory_cache_delete():
    c = _InMemoryCache(ttl_seconds=60)
    c.set("AAPL", "online", Decimal("150"))
    c.delete("AAPL", "online")
    assert c.get("AAPL", "online") is None


def test_memory_cache_clear():
    c = _InMemoryCache(ttl_seconds=60)
    c.set("AAPL", "online", Decimal("150"))
    c.set("TSLA", "online", Decimal("200"))
    c.clear()
    assert c.size == 0


def test_memory_cache_different_sources_isolated():
    c = _InMemoryCache(ttl_seconds=60)
    c.set("AAPL", "online", Decimal("150"))
    c.set("AAPL", "mock", Decimal("100"))
    assert c.get("AAPL", "online") == Decimal("150")
    assert c.get("AAPL", "mock") == Decimal("100")


def test_memory_cache_backend_name():
    assert _InMemoryCache().backend == "memory"


# ── Redis cache (mocked) ───────────────────────────────────────────────────────

def _redis_mock(get_val: bytes | None = None) -> MagicMock:
    r = MagicMock()
    r.get.return_value = get_val
    return r


def test_redis_cache_hit():
    r = _redis_mock(get_val=b"150.00")
    c = _RedisCache(r, ttl_seconds=5)
    assert c.get("AAPL", "online") == Decimal("150.00")
    r.get.assert_called_once_with("price:online:AAPL")


def test_redis_cache_miss():
    r = _redis_mock(get_val=None)
    c = _RedisCache(r, ttl_seconds=5)
    assert c.get("AAPL", "online") is None


def test_redis_cache_set_calls_setex():
    r = MagicMock()
    c = _RedisCache(r, ttl_seconds=10)
    c.set("AAPL", "online", Decimal("150"))
    r.setex.assert_called_once_with("price:online:AAPL", 10, "150")


def test_redis_cache_delete():
    r = MagicMock()
    c = _RedisCache(r, ttl_seconds=5)
    c.delete("AAPL", "online")
    r.delete.assert_called_once_with("price:online:AAPL")


def test_redis_cache_get_returns_none_on_error():
    r = MagicMock()
    r.get.side_effect = ConnectionError("Redis down")
    c = _RedisCache(r, ttl_seconds=5)
    assert c.get("AAPL", "online") is None  # graceful fallback


def test_redis_cache_set_survives_error():
    r = MagicMock()
    r.setex.side_effect = ConnectionError("Redis down")
    c = _RedisCache(r, ttl_seconds=5)
    c.set("AAPL", "online", Decimal("150"))  # must not raise


def test_redis_cache_backend_name():
    c = _RedisCache(MagicMock(), ttl_seconds=5)
    assert c.backend == "redis"


# ── Cache factory: fallback to memory when Redis is unavailable ────────────────

def test_cache_factory_falls_back_when_redis_unreachable():
    """When REDIS_URL is set but the server is down, factory returns in-memory cache."""
    with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:16379"}):
        from services.cache import _build_cache
        cache = _build_cache()
    assert cache.backend == "memory"


def test_cache_factory_uses_memory_when_no_redis_url():
    from services.cache import _build_cache
    with patch.dict("os.environ", {}, clear=True):
        cache = _build_cache()
    assert cache.backend == "memory"
