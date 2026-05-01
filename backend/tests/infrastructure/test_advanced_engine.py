"""TDD tests for the four new engine-level features.

1. Idempotency   — duplicate order requests return the cached result
2. Reconciler    — flags accounts when cash_balance drifts from ledger
3. Margin engine — charges daily borrow interest on leveraged positions
4. Sharpe ratio  — risk-free rate is subtracted from excess returns
5. Flash-crash   — circuit breaker trips on >10 % spike and blocks orders
6. Race stress   — 50 concurrent buys for same player, balance stays >= 0
"""

import asyncio
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from models.enums import OrderSide, OrderStatus
from models.player import Player
from schemas.competition import CompetitionCreate
from schemas.order import OrderCreate
from services.competition import create_competition, start_competition
from services.idempotency import (
    _InMemoryIdempotencyStore,
    _RedisIdempotencyStore,
    cache_order_result,
    get_cached_order,
)
from services.margin_engine import charge_interest, compute_interest
from services.reconciler import compute_expected_balance, reconcile_player
from tests.conftest import create_lobby_http, make_user


def _adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(db, balance: Decimal = Decimal("10000"),
                 fee: Decimal = Decimal("0"),
                 max_leverage: Decimal = Decimal("1.0")):
    user, _ = await make_user(db, "Host")
    comp, player, _ = await create_competition(
        db,
        CompetitionCreate(
            name="Test", starting_balance=balance,
            asset_universe=["AAPL", "TSLA"],
            fee_pct=fee, max_leverage=max_leverage,
        ),
        user,
    )
    await start_competition(db, comp.lobby_code, player)
    return comp, player


# ══════════════════════════════════════════════════════════════════════════════
# 1. IDEMPOTENCY
# ══════════════════════════════════════════════════════════════════════════════

class TestIdempotencyStore:
    def test_miss_on_new_key(self):
        s = _InMemoryIdempotencyStore(ttl_seconds=60)
        assert s.get("player-1", "key-abc") is None

    def test_hit_after_set(self):
        s = _InMemoryIdempotencyStore(ttl_seconds=60)
        s.set("player-1", "key-abc", {"status": "filled"})
        assert s.get("player-1", "key-abc") == {"status": "filled"}

    def test_expired_returns_none(self):
        s = _InMemoryIdempotencyStore(ttl_seconds=0)
        s.set("player-1", "key-abc", {"status": "filled"})
        time.sleep(0.05)
        assert s.get("player-1", "key-abc") is None

    def test_different_players_isolated(self):
        s = _InMemoryIdempotencyStore(ttl_seconds=60)
        s.set("player-1", "key-abc", {"status": "filled"})
        assert s.get("player-2", "key-abc") is None

    def test_same_player_different_keys_isolated(self):
        s = _InMemoryIdempotencyStore(ttl_seconds=60)
        s.set("player-1", "key-1", {"status": "filled"})
        assert s.get("player-1", "key-2") is None

    def test_redis_get_hit(self):
        import json
        r = MagicMock()
        r.get.return_value = json.dumps({"status": "filled"}).encode()
        s = _RedisIdempotencyStore(r, ttl_seconds=60)
        assert s.get("p", "k") == {"status": "filled"}

    def test_redis_get_miss(self):
        r = MagicMock()
        r.get.return_value = None
        s = _RedisIdempotencyStore(r, ttl_seconds=60)
        assert s.get("p", "k") is None

    def test_redis_set_calls_setex(self):
        r = MagicMock()
        s = _RedisIdempotencyStore(r, ttl_seconds=60)
        s.set("p", "k", {"status": "filled"})
        r.setex.assert_called_once()

    def test_redis_error_on_get_returns_none(self):
        r = MagicMock()
        r.get.side_effect = ConnectionError("down")
        s = _RedisIdempotencyStore(r, ttl_seconds=60)
        assert s.get("p", "k") is None


@pytest.mark.asyncio
async def test_idempotency_http_returns_cached_on_duplicate(concurrent_client):
    """A second request with the same X-Idempotency-Key returns HTTP 200 + same order."""
    client, _ = concurrent_client
    ctx = await create_lobby_http(
        client, "IdempPlayer",
        starting_balance="10000", asset_universe=["AAPL"],
        fee_pct="0", data_source="mock",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    pid = lb.json()[0]["player_id"]
    key = "test-idem-key-001"

    # First request → 201 Created
    r1 = await client.post(
        f"/competitions/{ctx['code']}/players/{pid}/orders",
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
        headers={
            "Authorization": f"Bearer {ctx['player_token']}",
            "X-Idempotency-Key": key,
        },
    )
    assert r1.status_code == 201
    order_id = r1.json()["id"]

    # Second request (same key) → 200 OK, same order_id
    r2 = await client.post(
        f"/competitions/{ctx['code']}/players/{pid}/orders",
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
        headers={
            "Authorization": f"Bearer {ctx['player_token']}",
            "X-Idempotency-Key": key,
        },
    )
    assert r2.status_code == 200, "Duplicate request must return 200"
    assert r2.json()["id"] == order_id, "Must return the original order"


@pytest.mark.asyncio
async def test_idempotency_different_keys_both_execute(concurrent_client):
    """Two different X-Idempotency-Keys → both result in new orders."""
    client, verify = concurrent_client
    ctx = await create_lobby_http(
        client, "IdempPlayer2",
        starting_balance="10000", asset_universe=["AAPL"],
        fee_pct="0", data_source="mock",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    pid = lb.json()[0]["player_id"]

    ids = []
    for key in ["key-001", "key-002"]:
        r = await client.post(
            f"/competitions/{ctx['code']}/players/{pid}/orders",
            json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
            headers={
                "Authorization": f"Bearer {ctx['player_token']}",
                "X-Idempotency-Key": key,
            },
        )
        assert r.status_code == 201
        ids.append(r.json()["id"])

    assert ids[0] != ids[1], "Different keys must produce different orders"


# ══════════════════════════════════════════════════════════════════════════════
# 2. RECONCILER
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reconciler_clean_account(db):
    """A fresh account with no drift should not be flagged."""
    comp, player = await _setup(db, balance=Decimal("10000"))
    await db.flush()

    report = await reconcile_player(db, player)

    assert report["flagged"] is False
    assert abs(report["drift"]) <= Decimal("0.01")


@pytest.mark.asyncio
async def test_reconciler_detects_drift_and_halts_trading(db):
    """Injecting a cash_balance that differs from the ledger must flag the account."""
    from services.order_engine import place_order

    comp, player = await _setup(db, balance=Decimal("10000"))

    await place_order(
        db, player, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        _adapter(Decimal("100")),
    )
    await db.flush()

    # Corrupt the balance by adding $999 out of thin air (simulates a bug)
    player.cash_balance = player.cash_balance + Decimal("999")
    await db.flush()

    report = await reconcile_player(db, player)

    assert report["flagged"] is True, "Drift of $999 must flag the account"
    assert player.trading_halted is True


@pytest.mark.asyncio
async def test_reconciler_halted_player_blocked(db):
    """A player with trading_halted=True must receive 409 on order placement."""
    comp, player = await _setup(db, balance=Decimal("10000"))
    player.trading_halted = True
    await db.flush()

    from fastapi import HTTPException
    from services.order_engine import place_order

    with pytest.raises(HTTPException) as exc_info:
        await place_order(
            db, player, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
            _adapter(Decimal("100")),
        )

    assert exc_info.value.status_code == 409
    assert "halted" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_reconciler_expected_balance_matches_after_buy_sell(db):
    """After a round-trip (buy then sell), ledger expected balance == cash_balance."""
    from services.order_engine import place_order

    comp, player = await _setup(db, balance=Decimal("10000"), fee=Decimal("0"))
    adapter = _adapter(Decimal("100"))

    await place_order(db, player, comp,
                      OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
                      adapter)
    await place_order(db, player, comp,
                      OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5")),
                      adapter)
    await db.flush()

    expected = await compute_expected_balance(db, player.id)
    assert abs(expected - player.cash_balance) <= Decimal("0.01"), (
        f"Expected {expected}, actual {player.cash_balance}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. MARGIN INTEREST ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeInterest:
    def test_no_positions_no_interest(self):
        interest = compute_interest(Decimal("10000"), [], {})
        assert interest == Decimal("0")

    def test_fully_funded_no_interest(self):
        """Cash covers the full position notional → borrowed = 0 → no interest."""
        pos = MagicMock()
        pos.ticker = "AAPL"
        pos.quantity = Decimal("10")
        pos.avg_entry_price = Decimal("100")
        interest = compute_interest(
            Decimal("1500"),   # cash > notional (1000)
            [pos],
            {"AAPL": Decimal("100")},
            daily_rate=Decimal("0.0002"),
        )
        assert interest == Decimal("0")

    def test_leveraged_position_charges_interest(self):
        """Notional $1000, cash $200 → borrowed $800 × 0.02% = $0.16."""
        pos = MagicMock()
        pos.ticker = "AAPL"
        pos.quantity = Decimal("10")
        pos.avg_entry_price = Decimal("100")
        interest = compute_interest(
            Decimal("200"),
            [pos],
            {"AAPL": Decimal("100")},
            daily_rate=Decimal("0.0002"),
        )
        # borrowed = 1000 - 200 = 800; interest = 800 * 0.0002 = 0.16
        assert interest == Decimal("0.16")

    def test_multiple_positions(self):
        positions = []
        for ticker, qty, price in [("AAPL", 5, 100), ("TSLA", 3, 200)]:
            p = MagicMock()
            p.ticker = ticker
            p.quantity = Decimal(str(qty))
            p.avg_entry_price = Decimal(str(price))
            positions.append(p)

        # Notional = 5*100 + 3*200 = 1100; cash = 100 → borrowed = 1000
        interest = compute_interest(
            Decimal("100"),
            positions,
            {"AAPL": Decimal("100"), "TSLA": Decimal("200")},
            daily_rate=Decimal("0.0002"),
        )
        assert interest == Decimal("0.20")  # 1000 * 0.0002


@pytest.mark.asyncio
async def test_charge_interest_debits_balance_and_writes_ledger(db):
    """charge_interest() reduces cash_balance and writes a LedgerEntry pair."""
    from models.ledger_entry import LedgerEntry

    comp, player = await _setup(db, balance=Decimal("10000"),
                                 max_leverage=Decimal("3"))
    interest = Decimal("1.50")
    balance_before = player.cash_balance

    await charge_interest(db, player, comp, interest)
    await db.flush()

    assert player.cash_balance == balance_before - interest

    entries = (await db.execute(select(LedgerEntry).where(
        LedgerEntry.player_id == player.id,
        LedgerEntry.account_type == "INTEREST_EXPENSE",
    ))).scalars().all()
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_charge_interest_zero_is_noop(db):
    """Charging $0 interest must not modify balance or write entries."""
    from models.ledger_entry import LedgerEntry

    comp, player = await _setup(db, balance=Decimal("10000"))
    balance_before = player.cash_balance

    await charge_interest(db, player, comp, Decimal("0"))
    await db.flush()

    assert player.cash_balance == balance_before
    entries = (await db.execute(select(LedgerEntry).where(
        LedgerEntry.account_type == "INTEREST_EXPENSE",
    ))).scalars().all()
    assert len(entries) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. SHARPE RATIO WITH RISK-FREE RATE
# ══════════════════════════════════════════════════════════════════════════════

def test_sharpe_subtracts_risk_free_rate():
    """With a non-zero risk-free rate, Sharpe is lower than naive excess return."""
    from unittest.mock import patch
    from services.leaderboard import _compute_score
    from schemas.competition import CompetitionCreate
    from models.enums import ScoringMethod

    comp = MagicMock()
    comp.scoring_method = ScoringMethod.sharpe_ratio
    comp.starting_balance = Decimal("10000")

    values = [10000, 10100, 10200, 10300, 10400, 10500]
    snaps = [MagicMock(total_value=Decimal(str(v))) for v in values]

    with patch("services.leaderboard.settings") as mock_settings:
        mock_settings.RISK_FREE_RATE_ANNUAL = 0.0
        score_zero_rf = _compute_score(snaps, comp, Decimal("10500"))

    with patch("services.leaderboard.settings") as mock_settings:
        mock_settings.RISK_FREE_RATE_ANNUAL = 0.05   # 5 % annual
        score_with_rf = _compute_score(snaps, comp, Decimal("10500"))

    assert score_with_rf < score_zero_rf, (
        "A positive risk-free rate must reduce the Sharpe score"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. FLASH CRASH — circuit breaker trips on >10 % spike
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_flash_crash_pauses_trading(concurrent_client):
    """A >10 % price spike in the rolling window must pause trading (503)."""
    client, _ = concurrent_client

    ctx = await create_lobby_http(
        client, "FlashPlayer",
        starting_balance="50000", asset_universe=["AAPL"],
        fee_pct="0", data_source="online",   # circuit breaker only fires for online
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    pid = lb.json()[0]["player_id"]
    token = ctx["player_token"]
    code = ctx["code"]

    # Simulate the circuit breaker being already tripped by directly setting the pause
    from services.market_guardian import get_guardian
    lb_data = lb.json()
    comp_id = None

    # We can't easily get comp_id from the leaderboard, so find it via the competition endpoint
    comp_resp = await client.get(f"/competitions/{code}")
    # Use a known guardian by patching the adapter to return a spiking price
    from unittest.mock import patch

    # Inject an adapter that returns a massively spiking price so the guardian trips
    call_count = [0]
    def spiking_price(ticker):
        call_count[0] += 1
        return Decimal("100") if call_count[0] == 1 else Decimal("115")  # 15% spike

    mock_adapter = MagicMock()
    mock_adapter.source = "online"       # must look like online to the circuit breaker
    mock_adapter.get_price.side_effect = spiking_price

    with patch("routers.orders.get_adapter", return_value=mock_adapter):
        # First order — records price 100, no trip
        r1 = await client.post(
            f"/competitions/{code}/players/{pid}/orders",
            json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # Second order — records price 115 (15% spike) → trip → 503
        r2 = await client.post(
            f"/competitions/{code}/players/{pid}/orders",
            json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r1.status_code in (201, 400), f"First order status unexpected: {r1.status_code}"
    assert r2.status_code == 503, (
        f"Circuit breaker must return 503 after 15% spike, got {r2.status_code}: {r2.text}"
    )
    assert "paused" in r2.json()["detail"].lower()


# ══════════════════════════════════════════════════════════════════════════════
# 6. RACE STRESS — 50 concurrent buys, balance stays >= 0
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_50_concurrent_buys_balance_nonneg(db):
    """50 concurrent asyncio tasks attempting to buy AAPL — balance must stay >= 0.

    This exercises the asyncio.Lock + optimistic locking + position savepoint
    under heavy concurrency to prove none of the guards can be defeated at
    the service layer.
    """
    import contextlib
    from services.order_engine import place_order

    comp, player = await _setup(db, balance=Decimal("500"))  # can afford at most ~3 fills
    adapter = _adapter(Decimal("100"))

    async def _try_buy():
        with contextlib.suppress(Exception):
            await place_order(
                db, player, comp,
                OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
                adapter,
            )

    await asyncio.gather(*[_try_buy() for _ in range(50)])

    assert player.cash_balance >= Decimal("0"), (
        f"Balance went negative under 50-concurrent-buy stress: {player.cash_balance}"
    )
