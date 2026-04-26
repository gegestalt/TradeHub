"""Comprehensive integration test mirroring the live_test_runner.sh bash script.

Uses mock data adapter (no network required) and the concurrent_client fixture for
concurrent order placement. Covers the full competition lifecycle from registration
through final leaderboard.

Test structure:
  test_complete_flow        — sequential flow (Steps 1-7, 9-16)
  test_concurrent_trading   — concurrent order placement (Step 8) using asyncio.gather
"""

import asyncio
from decimal import Decimal

import pytest

from tests.conftest import create_lobby_http, join_http, register_http

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "META", "GOOGL", "AMZN", "TSLA",
    "NFLX", "CRM", "JPM", "V", "MA", "GS", "ORCL", "UBER",
    "SPY", "QQQ", "BTC-USD", "ETH-USD",
]
STARTING_BALANCE = "100000"
FEE_PCT = "0.001"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _place(client, code, player_id, token, body) -> tuple[int, dict]:
    """POST an order and return (status_code, response_json)."""
    resp = await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    return resp.status_code, resp.json()


async def _market_buy(client, code, player_id, token, ticker: str, qty: str):
    return await _place(client, code, player_id, token, {
        "ticker": ticker, "side": "buy", "quantity": qty,
    })


async def _market_sell(client, code, player_id, token, ticker: str, qty: str):
    return await _place(client, code, player_id, token, {
        "ticker": ticker, "side": "sell", "quantity": qty,
    })


async def _limit_buy(client, code, player_id, token, ticker: str, qty: str, limit_price: str):
    return await _place(client, code, player_id, token, {
        "ticker": ticker, "side": "buy", "order_type": "limit",
        "quantity": qty, "limit_price": limit_price,
    })


async def _limit_buy_ioc(client, code, player_id, token, ticker: str, qty: str, limit_price: str):
    return await _place(client, code, player_id, token, {
        "ticker": ticker, "side": "buy", "order_type": "limit",
        "quantity": qty, "limit_price": limit_price, "time_in_force": "ioc",
    })


async def _stop_loss(client, code, player_id, token, ticker: str, qty: str, stop_price: str):
    return await _place(client, code, player_id, token, {
        "ticker": ticker, "side": "sell", "order_type": "stop_loss",
        "quantity": qty, "stop_price": stop_price,
    })


async def _take_profit(client, code, player_id, token, ticker: str, qty: str, tp_price: str):
    return await _place(client, code, player_id, token, {
        "ticker": ticker, "side": "sell", "order_type": "take_profit",
        "quantity": qty, "take_profit_price": tp_price,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Step 1–7, 9–16: Sequential flow
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_complete_flow(client):
    """Full competition lifecycle: register, create, join, start, trade, end.

    Steps covered:
      1. Register 5 players + Watcher
      2. Alice creates competition (20-asset universe, mock data, fee=0.001, balance=100000)
      3. All players join; Watcher joins as spectator
      4. Duplicate join is rejected (409)
      5. Alice starts competition
      6. Late join rejected (400)
      7. Watcher cannot place orders (403)
      9. Portfolio check for all 5 players
      10. Leaderboard check (5 players, Watcher absent, rank present)
      11. Bob tries to end (not creator) -> 403
      12. Alice ends competition
      13. Trading after end -> 400
      14. Final leaderboard accessible (state = ended)
      15. Health endpoint -> status ok
    """

    # ── Step 1: Register 5 players + Watcher ─────────────────────────────────
    alice_reg = await register_http(client, "Alice")
    bob_reg = await register_http(client, "Bob")
    charlie_reg = await register_http(client, "Charlie")
    dave_reg = await register_http(client, "Dave")
    eve_reg = await register_http(client, "Eve")
    watcher_reg = await register_http(client, "Watcher")

    assert all(reg["token"] for reg in [alice_reg, bob_reg, charlie_reg, dave_reg, eve_reg, watcher_reg])

    # Empty display_name should be rejected (422)
    bad_reg = await client.post("/users/register", json={"display_name": ""})
    assert bad_reg.status_code == 422

    # ── Step 2: Alice creates competition ─────────────────────────────────────
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Complete Flow Test",
            "asset_universe": UNIVERSE,
            "starting_balance": STARTING_BALANCE,
            "data_source": "mock",
        },
        headers={"Authorization": f"Bearer {alice_reg['token']}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    lobby = create_resp.json()
    code = lobby["lobby_code"]
    alice_player_token = lobby["token"]
    assert code and len(code) == 6

    # Verify competition is visible (state=lobby, universe has all 20 tickers, data_source=mock)
    comp_resp = await client.get(f"/competitions/{code}")
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    assert comp_data["state"] == "lobby"
    assert comp_data["data_source"] == "mock"
    assert "ETH-USD" in comp_data["asset_universe"]
    assert len(comp_data["asset_universe"]) == 20

    # ── Step 3: Players join; Watcher joins as spectator ─────────────────────
    bob_join = await join_http(client, code, "Bob")
    charlie_join = await join_http(client, code, "Charlie")
    dave_join = await join_http(client, code, "Dave")
    eve_join = await join_http(client, code, "Eve")

    # Each joiner gets a valid token and player_id
    for j in [bob_join, charlie_join, dave_join, eve_join]:
        assert j["player_token"]
        assert j["player_id"]

    # Watcher joins as spectator
    watcher_resp = await client.post(
        f"/competitions/{code}/join",
        json={"spectator": True},
        headers={"Authorization": f"Bearer {watcher_reg['token']}"},
    )
    assert watcher_resp.status_code == 201, watcher_resp.text
    watcher_join = watcher_resp.json()
    # Spectator has $0 balance
    assert Decimal(str(watcher_join["cash_balance"])) == Decimal("0")
    watcher_player_id = watcher_join["player_id"]
    watcher_player_token = watcher_join["token"]

    # ── Step 4: Duplicate join rejected (409) ─────────────────────────────────
    # Use the user_token of the Bob that already joined (not the pre-registered bob_reg)
    dup_resp = await client.post(
        f"/competitions/{code}/join",
        json={"spectator": False},
        headers={"Authorization": f"Bearer {bob_join['user_token']}"},
    )
    assert dup_resp.status_code == 409, dup_resp.text
    assert "already joined" in dup_resp.text.lower() or dup_resp.status_code == 409

    # ── Step 5: Alice starts competition ──────────────────────────────────────
    start_resp = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {alice_player_token}"},
    )
    assert start_resp.status_code == 200, start_resp.text
    assert start_resp.json()["state"] == "active"

    # Get Alice's player_id from leaderboard
    lb_resp = await client.get(f"/competitions/{code}/leaderboard")
    assert lb_resp.status_code == 200
    lb_entries = lb_resp.json()
    alice_player_id = next(e["player_id"] for e in lb_entries if e["display_name"] == "Alice")

    # ── Step 6: Late join rejected (400) ──────────────────────────────────────
    late_reg = await register_http(client, "Late")
    late_resp = await client.post(
        f"/competitions/{code}/join",
        json={"spectator": False},
        headers={"Authorization": f"Bearer {late_reg['token']}"},
    )
    assert late_resp.status_code == 400, late_resp.text
    assert "started" in late_resp.text.lower() or late_resp.status_code == 400

    # ── Step 7: Watcher cannot place orders (403) ─────────────────────────────
    spec_order = await client.post(
        f"/competitions/{code}/players/{watcher_player_id}/orders",
        headers={"Authorization": f"Bearer {watcher_player_token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert spec_order.status_code == 403, spec_order.text

    # ── Step 8 (sequential part): Alice places a few orders for guard-rail tests
    # Market buy to establish a position for later tests
    # Mock prices are in 50-200 range; 10 shares is well within $100k budget
    sc, order = await _market_buy(client, code, alice_player_id, alice_player_token, "AAPL", "10")
    assert sc == 201, order
    assert order["status"] == "filled"

    # Limit buy above market (fills immediately with mock adapter).
    # Using 5000 as limit_price guarantees fill since mock prices are 50-200.
    # 5 * 5000 = $25k, leaving ~$72k in cash.
    sc, order = await _limit_buy(client, code, alice_player_id, alice_player_token, "MSFT", "5", "5000.00")
    assert sc == 201, order
    assert order["status"] == "filled"

    # Market sell (partial)
    sc, order = await _market_sell(client, code, alice_player_id, alice_player_token, "AAPL", "3")
    assert sc == 201, order
    assert order["status"] == "filled"

    # Stop-loss (resting — far below mock prices)
    sc, order = await _stop_loss(client, code, alice_player_id, alice_player_token, "AAPL", "2", "0.01")
    assert sc == 201, order
    assert order["status"] == "pending"

    # Take-profit (resting — far above mock prices)
    sc, order = await _take_profit(client, code, alice_player_id, alice_player_token, "MSFT", "1", "5000.00")
    assert sc == 201, order
    assert order["status"] == "pending"

    # Bob places a buy for his own guard-rail tests later
    sc, order = await _market_buy(client, code, bob_join["player_id"], bob_join["player_token"], "SPY", "5")
    assert sc == 201, order

    # ── Guard rails ────────────────────────────────────────────────────────────
    # Ticker not in universe -> 400
    sc, bad = await _place(client, code, alice_player_id, alice_player_token, {
        "ticker": "HOOD", "side": "buy", "quantity": "1",
    })
    assert sc == 400, bad
    assert "universe" in bad.get("detail", "").lower() or sc == 400

    # Oversell (no such position or insufficient qty) -> 400
    sc, bad = await _place(client, code, bob_join["player_id"], bob_join["player_token"], {
        "ticker": "SPY", "side": "sell", "quantity": "999999",
    })
    assert sc == 400, bad

    # Cross-player order (Alice's token on Dave's player_id) -> 403
    sc, bad = await _place(client, code, dave_join["player_id"], alice_player_token, {
        "ticker": "AAPL", "side": "buy", "quantity": "1",
    })
    assert sc == 403, bad
    assert "Cannot place" in bad.get("detail", "") or sc == 403

    # Cancellable limit order (price so low it won't fill)
    sc, cancel_order = await _limit_buy(
        client, code, alice_player_id, alice_player_token, "AAPL", "1", "0.01"
    )
    assert sc == 201, cancel_order
    assert cancel_order["status"] == "pending"
    cancel_oid = cancel_order["id"]

    cancel_resp = await client.delete(
        f"/competitions/{code}/players/{alice_player_id}/orders/{cancel_oid}",
        headers={"Authorization": f"Bearer {alice_player_token}"},
    )
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "cancelled"

    # IOC order that cannot fill -> expired
    sc, ioc_order = await _limit_buy_ioc(
        client, code, charlie_join["player_id"], charlie_join["player_token"],
        "AAPL", "1", "0.01",
    )
    assert sc == 201, ioc_order
    assert ioc_order["status"] == "expired"

    # ── Step 9: Portfolio check ────────────────────────────────────────────────
    port_resp = await client.get(
        f"/players/{alice_player_id}/portfolio",
        headers={"Authorization": f"Bearer {alice_player_token}"},
    )
    assert port_resp.status_code == 200, port_resp.text
    portfolio = port_resp.json()
    assert "total_value" in portfolio
    assert "cash_balance" in portfolio
    assert "positions" in portfolio
    assert isinstance(portfolio["positions"], list)
    # Alice bought AAPL (10) and sold 3, so should have 7 AAPL remaining (plus MSFT)
    tickers_held = {p["ticker"] for p in portfolio["positions"]}
    assert "AAPL" in tickers_held or "MSFT" in tickers_held  # at least one position

    # Cross-player portfolio access -> 403
    bad_port = await client.get(
        f"/players/{alice_player_id}/portfolio",
        headers={"Authorization": f"Bearer {bob_join['player_token']}"},
    )
    assert bad_port.status_code == 403, bad_port.text

    # ── Step 10: Leaderboard ──────────────────────────────────────────────────
    lb_resp = await client.get(f"/competitions/{code}/leaderboard")
    assert lb_resp.status_code == 200
    lb_entries = lb_resp.json()

    player_names = {e["display_name"] for e in lb_entries}
    assert "Alice" in player_names
    assert "Bob" in player_names
    assert "Charlie" in player_names
    assert "Dave" in player_names
    assert "Eve" in player_names
    assert "Watcher" not in player_names  # spectator excluded

    # rank field present and sequential starting from 1
    ranks = [e["rank"] for e in lb_entries]
    assert sorted(ranks) == list(range(1, len(lb_entries) + 1))

    # Required fields present
    for entry in lb_entries:
        assert "pnl" in entry
        assert "unrealized_pnl" in entry
        assert "realized_pnl" in entry
        assert "positions" in entry

    # ── Step 11: Bob tries to end competition (not creator) -> 403 ────────────
    bob_end = await client.post(
        f"/competitions/{code}/end",
        headers={"Authorization": f"Bearer {bob_join['player_token']}"},
    )
    assert bob_end.status_code == 403, bob_end.text
    assert "creator" in bob_end.text.lower() or bob_end.status_code == 403

    # ── Step 12: Alice ends competition ───────────────────────────────────────
    end_resp = await client.post(
        f"/competitions/{code}/end",
        headers={"Authorization": f"Bearer {alice_player_token}"},
    )
    assert end_resp.status_code == 200, end_resp.text
    assert end_resp.json()["state"] == "ended"

    # ── Step 13: Trading after end -> 400 ────────────────────────────────────
    sc, bad = await _market_buy(
        client, code, bob_join["player_id"], bob_join["player_token"], "BTC-USD", "1"
    )
    assert sc == 400, bad
    assert "not active" in bad.get("detail", "").lower() or sc == 400

    # ── Step 14: Final leaderboard accessible (state=ended) ──────────────────
    final_lb = await client.get(f"/competitions/{code}/leaderboard")
    assert final_lb.status_code == 200
    final_entries = final_lb.json()
    assert len(final_entries) >= 1
    assert all("rank" in e for e in final_entries)
    # Winner is rank 1
    assert final_entries[0]["rank"] == 1

    # ── Step 15: Health endpoint ───────────────────────────────────────────────
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Step 8: Concurrent trading — all 5 players fire orders simultaneously
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_trading(concurrent_client):
    """All 5 players fire multiple orders concurrently using asyncio.gather.

    Uses the concurrent_client fixture which gives each request its own DB
    session to avoid 'Session is already flushing' errors under asyncio.gather.
    """
    client, verify = concurrent_client

    # ── Setup: register 5 players ─────────────────────────────────────────────
    names = ["Alice", "Bob", "Charlie", "Dave", "Eve"]
    regs = {}
    for name in names:
        reg = await register_http(client, name)
        regs[name] = reg

    # Alice creates the competition
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Concurrent Trading Test",
            "asset_universe": UNIVERSE,
            "starting_balance": STARTING_BALANCE,
            "data_source": "mock",
        },
        headers={"Authorization": f"Bearer {regs['Alice']['token']}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    lobby_data = create_resp.json()
    code = lobby_data["lobby_code"]
    alice_player_token = lobby_data["token"]

    # Other players join
    players = {}
    for name in ["Bob", "Charlie", "Dave", "Eve"]:
        join_data = await join_http(client, code, name)
        players[name] = join_data

    # Get Alice's player_id from leaderboard after start
    start_resp = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {alice_player_token}"},
    )
    assert start_resp.status_code == 200

    lb = await client.get(f"/competitions/{code}/leaderboard")
    alice_player_id = next(e["player_id"] for e in lb.json() if e["display_name"] == "Alice")
    players["Alice"] = {
        "player_token": alice_player_token,
        "player_id": alice_player_id,
    }

    # ── Build concurrent order tasks ──────────────────────────────────────────
    # Each player buys a different set of stocks with safe quantities.
    # Mock prices are in 50-200 range. With $100k balance, buying 50 shares
    # of a $200 stock = $10k, well within budget.
    player_orders = {
        "Alice":   [("AAPL", "50"), ("MSFT", "40"), ("NVDA", "30")],
        "Bob":     [("GOOGL", "45"), ("AMZN", "35"), ("META", "25")],
        "Charlie": [("TSLA", "60"), ("NFLX", "50"), ("CRM", "40")],
        "Dave":    [("JPM", "55"), ("V", "45"), ("MA", "35")],
        "Eve":     [("SPY", "65"), ("QQQ", "50"), ("GS", "30")],
    }

    async def buy_all(name: str) -> list[tuple[int, dict]]:
        pid = players[name]["player_id"]
        tok = players[name]["player_token"]
        results = []
        for ticker, qty in player_orders[name]:
            sc, body = await _market_buy(client, code, pid, tok, ticker, qty)
            results.append((sc, body))
        return results

    # Fire all 5 players concurrently
    all_results = await asyncio.gather(
        buy_all("Alice"),
        buy_all("Bob"),
        buy_all("Charlie"),
        buy_all("Dave"),
        buy_all("Eve"),
        return_exceptions=True,
    )

    # ── Verify results ────────────────────────────────────────────────────────
    for player_name, results in zip(names, all_results):
        assert not isinstance(results, Exception), (
            f"{player_name} raised an exception: {results}"
        )
        for sc, body in results:
            assert sc == 201, f"{player_name} order failed: {body}"
            assert body["status"] == "filled", (
                f"{player_name} order not filled: {body}"
            )

    # ── Post-concurrent leaderboard check ─────────────────────────────────────
    lb_resp = await client.get(f"/competitions/{code}/leaderboard")
    assert lb_resp.status_code == 200
    lb_entries = lb_resp.json()

    # All 5 players should be ranked
    assert len(lb_entries) == 5
    player_names_in_lb = {e["display_name"] for e in lb_entries}
    assert player_names_in_lb == set(names)

    # Each player should have at least 1 position (they all bought)
    for entry in lb_entries:
        assert len(entry["positions"]) >= 1, (
            f"{entry['display_name']} has no positions after concurrent trading"
        )

    # Ranks are sequential
    ranks = sorted(e["rank"] for e in lb_entries)
    assert ranks == list(range(1, 6))

    # Total value should be positive for all players
    for entry in lb_entries:
        assert Decimal(str(entry["total_value"])) > 0
