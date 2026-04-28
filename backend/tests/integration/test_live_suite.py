"""Comprehensive live integration test suite — pytest edition.

Replaces / extends the bash live_test_runner.sh with a structured pytest
suite that covers every feature added to TradeHub.

The bash script covers:
  - Registration, competition lifecycle, joining, starting
  - Sequential trading, guard rails, portfolios, leaderboard
  - Competition end, health, metrics
  - Concurrent trading (via background & jobs)

This suite adds what the bash script does NOT test:
  - Ledger invariant check (debits == credits after all trades)
  - Audit log entries after fills and rejections
  - Partial fill flow (status=partial, correct fill_price/fee_paid)
  - Cache backend reported in GET /health
  - GET /competitions/{code}/ledger endpoint
  - GET /competitions/{code}/ledger/invariant endpoint
  - GET /competitions/{code}/audit endpoint
  - Concurrent trading via asyncio.gather (ASGI-level, not shell background)
  - balance_version increments with every fill (optimistic lock counter)
  - OCO one-cancels-other pair integrity

Structure
---------
test_standalone_*  — tests that need no shared game state (always fast)
test_full_game     — the entire 5-player lifecycle in one sequential function
                     so all parts share the same game and its accumulated state
"""

import asyncio
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import create_lobby_http, join_http, register_http

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "META", "GOOGL", "AMZN", "TSLA",
    "NFLX", "CRM", "JPM", "V", "MA", "GS", "ORCL", "UBER",
    "SPY", "QQQ", "BTC-USD", "ETH-USD",
]
STARTING_BALANCE = "100000"
FEE = "0.001"


# ── Shared helpers ─────────────────────────────────────────────────────────────

async def _order(client, code, player_id, token, **fields):
    return await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        json=fields,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _start(client, ctx):
    r = await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    assert r.status_code == 200, r.text


async def _player_id_for(client, code, display_name):
    lb = await client.get(f"/competitions/{code}/leaderboard")
    return next(e["player_id"] for e in lb.json() if e["display_name"] == display_name)


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE TESTS (no shared game state required)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_standalone_registration(concurrent_client):
    """Part 1 — Register players; empty name rejected (422)."""
    client, _ = concurrent_client
    for name in ["Alice", "Bob", "Charlie", "Dave", "Eve"]:
        r = await client.post("/users/register", json={"display_name": name})
        assert r.status_code == 201
        assert "token" in r.json()

    r = await client.post("/users/register", json={"display_name": ""})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_standalone_competition_creation(concurrent_client):
    """Part 2 — Competition created with 20-asset mock universe."""
    client, _ = concurrent_client
    ctx = await create_lobby_http(
        client, "Alice",
        name="Live Suite Game",
        starting_balance=STARTING_BALANCE,
        asset_universe=UNIVERSE,
        fee_pct=FEE,
        data_source="mock",
    )
    r = await client.get(f"/competitions/{ctx['code']}")
    data = r.json()
    assert data["state"] == "lobby"
    assert len(data["asset_universe"]) == 20
    assert data["data_source"] == "mock"


@pytest.mark.asyncio
async def test_standalone_partial_fill(concurrent_client):
    """Part 8 — Balance $350, order 10 shares at $100 → fills 3 (status=partial).

    Verifies: fill_price set, fill_at set, fee_paid correct, balance correct.
    """
    client, verify = concurrent_client
    ctx = await create_lobby_http(
        client, "PartialPlayer",
        starting_balance="350",
        asset_universe=["AAPL"],
        fee_pct="0",
        max_leverage="100",
        data_source="online",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    pid = lb.json()[0]["player_id"]

    mock_adapter = MagicMock()
    mock_adapter.get_price.return_value = Decimal("100")

    with patch("routers.orders.get_adapter", return_value=mock_adapter):
        r = await _order(client, ctx["code"], pid, ctx["player_token"],
                         ticker="AAPL", side="buy", quantity="10")

    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "partial"
    assert int(data["quantity"]) == 3
    assert Decimal(data["fill_price"]) == Decimal("100")
    assert data["fill_at"] is not None
    assert Decimal(data["fee_paid"]) == Decimal("0")

    from sqlalchemy import select
    from models.player import Player
    verify.expire_all()
    result = await verify.execute(select(Player).where(Player.id == pid))
    player = result.scalar_one()
    assert player.cash_balance == Decimal("50")


@pytest.mark.asyncio
async def test_standalone_cache_backend_health(concurrent_client):
    """Part 14 — GET /health reports cache_backend (memory or redis)."""
    client, _ = concurrent_client
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["cache_backend"] in ("memory", "redis")
    assert isinstance(data["cache_entries"], int)


@pytest.mark.asyncio
async def test_standalone_oco_pair(concurrent_client):
    """Part 17 — OCO: stop-loss + take-profit share oco_pair_id, both pending."""
    client, _ = concurrent_client
    ctx = await create_lobby_http(
        client, "OCOPlayer",
        starting_balance="50000",
        asset_universe=["AAPL"],
        fee_pct="0",
        data_source="mock",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    pid = lb.json()[0]["player_id"]

    r = await client.post(
        f"/competitions/{ctx['code']}/players/{pid}/orders/oco",
        json={"ticker": "AAPL", "side": "sell", "quantity": "1",
              "stop_price": "0.01", "take_profit_price": "999999"},
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    assert r.status_code == 201
    pair = r.json()
    assert len(pair) == 2
    assert pair[0]["oco_pair_id"] == pair[1]["oco_pair_id"]
    assert pair[0]["oco_pair_id"] is not None
    assert pair[0]["status"] == "pending"
    assert pair[1]["status"] == "pending"


@pytest.mark.asyncio
async def test_standalone_balance_version(concurrent_client):
    """Part 18 — balance_version increments by 1 on every fill."""
    from sqlalchemy import select
    from models.player import Player
    from models.enums import OrderSide
    from schemas.competition import CompetitionCreate
    from schemas.order import OrderCreate
    from services.competition import create_competition, start_competition
    from services.order_engine import place_order
    from tests.conftest import make_user

    _, verify = concurrent_client

    user, _ = await make_user(verify, "VersionHost")
    comp, player, _ = await create_competition(
        verify,
        CompetitionCreate(
            name="Version Test", starting_balance=Decimal("10000"),
            asset_universe=["AAPL"], fee_pct=Decimal("0"),
        ),
        user,
    )
    await start_competition(verify, comp.lobby_code, player)
    await verify.flush()
    await verify.refresh(player)
    v0 = player.balance_version

    adapter = MagicMock()
    adapter.get_price.return_value = Decimal("100")

    for i in range(3):
        await place_order(
            verify, player, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
            adapter,
        )
        await verify.flush()
        await verify.refresh(player)
        assert player.balance_version == v0 + i + 1, (
            f"After fill {i+1}: expected version {v0+i+1}, got {player.balance_version}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# FULL GAME TEST — all lifecycle parts share one competition
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_full_game(concurrent_client):
    """Complete 5-player live game covering every feature.

    Parts run sequentially so all assertions share the same competition and its
    accumulated order/ledger/audit state.

    Part  3  Join + duplicate rejection
    Part  4  Late join blocked; spectator cannot trade
    Part  5  Sequential buys
    Part  6  Concurrent trading (asyncio.gather)
    Part  7  Guard rails
    Part  9  Portfolios
    Part 10  Leaderboard
    Part 11  Ledger invariant
    Part 12  Ledger entries endpoint
    Part 13  Audit log endpoint
    Part 15  End competition
    Part 16  Final standings + metrics + post-end ledger invariant
    """
    client, verify = concurrent_client

    # ── Part 3: Setup + duplicate join ────────────────────────────────────────
    ctx = await create_lobby_http(
        client, "Alice",
        name="Full Game",
        starting_balance=STARTING_BALANCE,
        asset_universe=UNIVERSE,
        fee_pct=FEE,
        max_leverage="2",
        data_source="mock",
    )
    code = ctx["code"]
    alice_token = ctx["player_token"]

    players = {}
    for name in ["Bob", "Charlie", "Dave", "Eve"]:
        p = await join_http(client, code, name)
        players[name] = p

    # Duplicate join (Bob tries again, still in lobby state → 409)
    r = await client.post(
        f"/competitions/{code}/join",
        json={"spectator": False},
        headers={"Authorization": f"Bearer {players['Bob']['user_token']}"},
    )
    assert r.status_code == 409, f"Expected 409, got {r.status_code}: {r.text}"
    assert "already joined" in r.json()["detail"].lower()

    # Watcher joins as spectator (balance must be $0)
    watcher_reg = await register_http(client, "Watcher")
    wr = await client.post(
        f"/competitions/{code}/join",
        json={"spectator": True},
        headers={"Authorization": f"Bearer {watcher_reg['token']}"},
    )
    assert wr.status_code == 201
    assert Decimal(wr.json()["cash_balance"]) == Decimal("0")
    watcher_token = wr.json()["token"]
    watcher_pid = wr.json()["player_id"]

    # ── Part 4: Start; late join + spectator order blocked ────────────────────
    await _start(client, ctx)
    alice_id = await _player_id_for(client, code, "Alice")

    late_reg = await register_http(client, "LatePlayer")
    r = await client.post(
        f"/competitions/{code}/join",
        json={"spectator": False},
        headers={"Authorization": f"Bearer {late_reg['token']}"},
    )
    assert r.status_code == 400
    assert "started" in r.json()["detail"].lower()

    r = await _order(client, code, watcher_pid, watcher_token,
                     ticker="AAPL", side="buy", quantity="1")
    assert r.status_code == 403

    # ── Part 5: Sequential buys (each player establishes a position) ──────────
    initial_buys = [
        (alice_id, alice_token, "AAPL", "10"),
        (players["Bob"]["player_id"],     players["Bob"]["player_token"],     "TSLA", "5"),
        (players["Charlie"]["player_id"], players["Charlie"]["player_token"], "NVDA", "8"),
        (players["Dave"]["player_id"],    players["Dave"]["player_token"],    "GOOGL", "6"),
        (players["Eve"]["player_id"],     players["Eve"]["player_token"],     "MSFT", "12"),
    ]
    for pid, token, ticker, qty in initial_buys:
        r = await _order(client, code, pid, token,
                         ticker=ticker, side="buy", quantity=qty)
        assert r.status_code == 201, f"Initial buy {ticker} failed: {r.text}"
        assert r.json()["status"] == "filled"
        assert Decimal(r.json()["fill_price"]) > 0

    # ── Part 6: Concurrent trading — all 5 players simultaneously ─────────────
    concurrent_orders = [
        # Alice: buy META + stop-loss on AAPL + limit above market
        (alice_id, alice_token, [
            {"ticker": "META",  "side": "buy", "quantity": "3"},
            {"ticker": "AAPL",  "side": "sell", "order_type": "stop_loss",
             "quantity": "2",   "stop_price": "0.01"},
            {"ticker": "AMZN",  "side": "buy", "order_type": "limit",
             "quantity": "2",   "limit_price": "99999"},
        ]),
        # Bob: buy NVDA + stop-loss TSLA + limit AMD
        (players["Bob"]["player_id"], players["Bob"]["player_token"], [
            {"ticker": "NVDA",  "side": "buy", "quantity": "3"},
            {"ticker": "TSLA",  "side": "sell", "order_type": "stop_loss",
             "quantity": "1",   "stop_price": "0.01"},
            {"ticker": "AMD",   "side": "buy", "quantity": "4"},
        ]),
        # Charlie: buy AAPL + take-profit NVDA + buy CRM
        (players["Charlie"]["player_id"], players["Charlie"]["player_token"], [
            {"ticker": "AAPL",  "side": "buy", "quantity": "4"},
            {"ticker": "NVDA",  "side": "sell", "order_type": "take_profit",
             "quantity": "1",   "take_profit_price": "999999"},
            {"ticker": "CRM",   "side": "buy", "quantity": "2"},
        ]),
        # Dave: buy MSFT + stop-loss GOOGL + buy JPM
        (players["Dave"]["player_id"], players["Dave"]["player_token"], [
            {"ticker": "MSFT",  "side": "buy", "quantity": "5"},
            {"ticker": "GOOGL", "side": "sell", "order_type": "stop_loss",
             "quantity": "1",   "stop_price": "0.01"},
            {"ticker": "JPM",   "side": "buy", "quantity": "3"},
        ]),
        # Eve: buy TSLA + take-profit MSFT + buy V
        (players["Eve"]["player_id"], players["Eve"]["player_token"], [
            {"ticker": "TSLA",  "side": "buy", "quantity": "2"},
            {"ticker": "MSFT",  "side": "sell", "order_type": "take_profit",
             "quantity": "2",   "take_profit_price": "999999"},
            {"ticker": "V",     "side": "buy", "quantity": "4"},
        ]),
    ]

    all_tasks = [
        client.post(
            f"/competitions/{code}/players/{pid}/orders",
            json=order_body,
            headers={"Authorization": f"Bearer {token}"},
        )
        for pid, token, orders in concurrent_orders
        for order_body in orders
    ]
    resps = await asyncio.gather(*all_tasks)

    for r in resps:
        assert r.status_code in (201, 400, 409, 429), (
            f"Unexpected status {r.status_code}: {r.text}"
        )

    filled_count = sum(
        1 for r in resps
        if r.status_code == 201 and r.json().get("status") in ("filled", "partial")
    )
    assert filled_count >= 5, f"Expected >= 5 filled concurrent orders, got {filled_count}"

    # All balances non-negative after concurrent fills
    from sqlalchemy import select
    from models.player import Player
    for pid, _, _ in concurrent_orders:
        verify.expire_all()
        result = await verify.execute(select(Player).where(Player.id == pid))
        player = result.scalar_one()
        assert player.cash_balance >= Decimal("0"), (
            f"Player {pid} balance negative after concurrent trading: {player.cash_balance}"
        )

    # ── Part 7: Guard rails ────────────────────────────────────────────────────
    # Bad ticker
    r = await _order(client, code, alice_id, alice_token,
                     ticker="FAKECOIN999", side="buy", quantity="1")
    assert r.status_code == 400
    assert "asset universe" in r.json()["detail"].lower()

    # Oversell
    r = await _order(client, code, alice_id, alice_token,
                     ticker="AAPL", side="sell", quantity="999999")
    assert r.status_code == 400

    # Cross-player
    r = await _order(client, code, players["Bob"]["player_id"], alice_token,
                     ticker="AAPL", side="buy", quantity="1")
    assert r.status_code == 403

    # Cancel a pending limit order — use Bob who still has ample balance
    bob_pid = players["Bob"]["player_id"]
    bob_token = players["Bob"]["player_token"]
    r = await _order(client, code, bob_pid, bob_token,
                     ticker="TSLA", side="buy", order_type="limit",
                     quantity="1", limit_price="0.01")
    assert r.status_code == 201, f"Limit order failed: {r.text}"
    assert r.json()["status"] == "pending"
    cancel_oid = r.json()["id"]

    r = await client.delete(
        f"/competitions/{code}/players/{bob_pid}/orders/{cancel_oid}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    # IOC order that cannot fill → expires
    r = await _order(client, code, bob_pid, bob_token,
                     ticker="TSLA", side="buy", order_type="limit",
                     quantity="1", limit_price="0.01", time_in_force="ioc")
    assert r.status_code == 201
    assert r.json()["status"] == "expired"

    # ── Part 9: Portfolios ────────────────────────────────────────────────────
    for pid, token in [(alice_id, alice_token),
                       *[(p["player_id"], p["player_token"]) for p in players.values()]]:
        r = await client.get(
            f"/players/{pid}/portfolio",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert Decimal(data["total_value"]) > 0
        assert Decimal(data["cash_balance"]) >= 0
        assert "positions" in data

    # Cross-player portfolio blocked
    r = await client.get(
        f"/players/{players['Bob']['player_id']}/portfolio",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 403

    # ── Part 10: Leaderboard ──────────────────────────────────────────────────
    r = await client.get(f"/competitions/{code}/leaderboard")
    assert r.status_code == 200
    entries = r.json()
    names = {e["display_name"] for e in entries}
    assert {"Alice", "Bob", "Charlie", "Dave", "Eve"}.issubset(names)
    assert "Watcher" not in names
    assert [e["rank"] for e in entries] == sorted(e["rank"] for e in entries)
    for e in entries:
        assert Decimal(e["total_value"]) > 0
        assert "unrealized_pnl" in e
        assert "orders_filled" in e

    # ── Part 11: Ledger invariant ─────────────────────────────────────────────
    r = await client.get(f"/competitions/{code}/ledger/invariant")
    assert r.status_code == 200
    inv = r.json()
    assert inv["balanced"] is True, (
        f"LEDGER IMBALANCED: delta={inv['delta']}, "
        f"debit={inv['debit_total']}, credit={inv['credit_total']}"
    )
    assert Decimal(inv["delta"]) == Decimal("0"), f"Non-zero delta: {inv['delta']}"

    # The debit total must substantially exceed starting balances alone
    # (5 players × $100k = $500k starting; fills add position entries on top).
    # Starting balance pairs: 5 × 100000 = 500000 in both debit and credit.
    # Fill entries add at least: 5 buys × ~$1000 cost each = ~$5000 extra.
    # So total debits must be well above 500000.
    # Must exceed the starting-balance total (5 × $100k = $500k) by at least
    # the cost of the 5 sequential buys (min. 5 × 5 shares × $50 floor = $1250).
    starting_total = Decimal("500000")
    min_fill_cost = Decimal("500")   # conservative: at least $500 in fills
    assert Decimal(inv["debit_total"]) > starting_total + min_fill_cost, (
        "Debit total barely exceeds starting balances — fill ledger entries "
        f"may not be written. Got: {inv['debit_total']}"
    )

    # ── Part 12: Ledger entries endpoint ──────────────────────────────────────
    r = await client.get(f"/competitions/{code}/ledger")
    assert r.status_code == 200
    all_ledger = r.json()
    assert len(all_ledger) >= 10, f"Expected >= 10 entries, got {len(all_ledger)}"

    for entry in all_ledger:
        assert entry["side"] in ("debit", "credit")
        assert Decimal(entry["amount"]) > 0
        assert entry["journal_id"]

    # Alice-specific entries
    r = await client.get(f"/competitions/{code}/ledger?player_id={alice_id}")
    assert r.status_code == 200
    alice_ledger = r.json()
    assert all(e["player_id"] == alice_id for e in alice_ledger)
    assert len(alice_ledger) >= 2

    # Starting balance entry must exist for Alice
    starting = [
        e for e in alice_ledger
        if e["event_type"] == "CASH_AVAILABLE" and e["side"] == "debit"
        and e.get("description") and "Starting" in e["description"]
    ]
    assert len(starting) >= 1, "Starting balance ledger entry missing for Alice"
    assert Decimal(starting[0]["amount"]) == Decimal(STARTING_BALANCE)

    # ── Part 13: Audit log endpoint ───────────────────────────────────────────
    r = await client.get(f"/competitions/{code}/audit")
    assert r.status_code == 200
    all_audit = r.json()
    assert len(all_audit) >= 5, f"Expected >= 5 audit events, got {len(all_audit)}"

    event_types = {e["event_type"] for e in all_audit}
    assert "order_fill" in event_types
    assert "position_change" in event_types

    # Filter by player
    r = await client.get(f"/competitions/{code}/audit?player_id={alice_id}")
    assert r.status_code == 200
    alice_audit = r.json()
    assert all(e["player_id"] == alice_id for e in alice_audit)
    assert len(alice_audit) >= 1

    # Filter by event type
    r = await client.get(f"/competitions/{code}/audit?event_type=order_fill")
    assert r.status_code == 200
    fills = r.json()
    assert all(e["event_type"] == "order_fill" for e in fills)
    assert len(fills) >= 5

    for fill in fills[:3]:
        payload = json.loads(fill["payload"])
        assert "fill_price" in payload
        assert "balance_before" in payload
        assert "balance_after" in payload

    # ── Part 15: End competition ──────────────────────────────────────────────
    # Non-creator blocked
    r = await client.post(
        f"/competitions/{code}/end",
        headers={"Authorization": f"Bearer {players['Bob']['player_token']}"},
    )
    assert r.status_code == 403

    # Creator ends it
    r = await client.post(
        f"/competitions/{code}/end",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "ended"

    # Trading blocked post-end
    r = await _order(client, code, alice_id, alice_token,
                     ticker="AAPL", side="buy", quantity="1")
    assert r.status_code == 400
    assert "not active" in r.json()["detail"].lower()

    # ── Part 16: Final standings, metrics, ledger still balanced ─────────────
    r = await client.get(f"/competitions/{code}/leaderboard")
    assert r.status_code == 200
    standings = r.json()
    assert len(standings) == 5
    assert all(e["rank"] > 0 for e in standings)

    r = await client.get("/metrics")
    assert r.status_code == 200
    metrics_data = r.json()
    assert metrics_data["orders_placed"] >= metrics_data["orders_filled"]

    # Ledger invariant still holds after end
    r = await client.get(f"/competitions/{code}/ledger/invariant")
    assert r.status_code == 200
    assert r.json()["balanced"] is True
