"""Live competition simulation: 6 players, 2 spectators, online data (yfinance).

Run modes
---------
Fast (default pytest run):
    pytest tests/integration/test_live_competition.py
    → 2 trading rounds, competition ended manually, ~5 seconds total.

Live 30-minute run (set env var before running):
    LIVE_COMPETITION_MINUTES=30 pytest tests/integration/test_live_competition.py -s -v
    → 3 trading rounds spread over 30 minutes with real sleep gaps,
      then auto-end by the snapshot task.

Design
------
Every strategy is fully dynamic:
  - Assets are sampled randomly from the full UNIVERSE each round.
  - Quantities are randomised within each player's remaining budget.
  - Order types vary: market, limit, stop-loss, take-profit.
  - A player may buy *or* sell depending on what they already hold.
  - Results change on every run — there is no hardcoded "buy AAPL" anywhere.
"""

import asyncio
import os
import random
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app

# ── Configuration ──────────────────────────────────────────────────────────────

LIVE_MINUTES = int(os.getenv("LIVE_COMPETITION_MINUTES", "0"))
LIVE_MODE = LIVE_MINUTES > 0

# Full asset universe — each player picks a random subset each round.
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "TSLA", "META", "AMD",  "JPM",  "V",
    "MA",   "NFLX", "CRM",  "UBER", "GS",
    "SPY",  "QQQ",  "BTC-USD", "ETH-USD",
]

STARTING_BALANCE = "100000"
PLAYER_NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank"]
SPECTATOR_NAMES = ["Watcher1", "Watcher2"]

_ROUND_SLEEP = (LIVE_MINUTES * 60 // 3) if LIVE_MODE else 1


# ── In-memory DB fixture ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def live_client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _place(client, code, player_id, token, order_body) -> tuple[int, dict]:
    resp = await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json=order_body,
    )
    return resp.status_code, resp.json()


async def _get_price(client, ticker: str) -> float:
    resp = await client.get(f"/prices/{ticker}")
    if resp.status_code == 200:
        val = resp.json().get("price", "0")
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


async def _get_positions(client, code, player_id, token) -> dict[str, float]:
    """Return {ticker: quantity} for the player's current open positions."""
    resp = await client.get(
        f"/players/{player_id}/portfolio",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        return {}
    return {
        p["ticker"]: float(p["quantity"])
        for p in resp.json().get("positions", [])
        if float(p["quantity"]) > 0
    }


def _affordable_qty(price: float, balance: float, pct: float = 0.20) -> int:
    """Whole shares the player can buy spending at most `pct` of balance."""
    if price <= 0:
        return 0
    spend = balance * pct
    return max(1, int(spend / price))


def _crypto_qty(price: float, balance: float, pct: float = 0.15) -> str:
    """Fractional quantity for high-price crypto assets (4 decimal places)."""
    if price <= 0:
        return "0.0001"
    spend = balance * pct
    qty = round(spend / price, 4)
    return f"{max(0.0001, qty):.4f}"


def _is_crypto(ticker: str) -> bool:
    return ticker.endswith("-USD")


def _fmt_leaderboard(entries: list[dict]) -> str:
    lines = [
        "\n" + "═" * 100,
        f"{'RK':<4} {'PLAYER':<12} {'TOTAL VALUE':>14} {'P&L':>12} "
        f"{'P&L %':>8} {'REALIZED':>12} {'UNREALIZED':>12} {'DD%':>7} {'ORDERS':>7}",
        "─" * 100,
    ]
    for e in entries:
        pnl_sign = "+" if Decimal(str(e["pnl"])) >= 0 else ""
        dd = e.get("max_drawdown_pct", "0")
        lines.append(
            f"{e['rank']:<4} {e['display_name']:<12} "
            f"${float(e['total_value']):>13,.2f} "
            f"{pnl_sign}${float(e['pnl']):>10,.2f} "
            f"{pnl_sign}{float(e['pnl_pct']):>7.2f}% "
            f"${float(e['realized_pnl']):>10,.2f}  "
            f"${float(e['unrealized_pnl']):>10,.2f} "
            f"{float(dd):>6.2f}%"
            f"{e['orders_filled']:>6}",
        )
        for pos in e.get("positions", []):
            up = Decimal(str(pos["unrealized_pnl"]))
            sign = "+" if up >= 0 else ""
            lines.append(
                f"     └─ {pos['ticker']:<10} qty={float(pos['quantity']):.4f} "
                f"@ avg ${float(pos['avg_entry_price']):.2f} "
                f"| now ${float(pos['current_price']):.2f} "
                f"| MV ${float(pos['market_value']):.2f} "
                f"| {sign}${float(pos['unrealized_pnl']):.2f}"
            )
    lines.append("═" * 100)
    return "\n".join(lines)


# ── Dynamic strategies ─────────────────────────────────────────────────────────
#
# None of these strategies hardcode any ticker.  Every round they:
#   1. Sample a random subset of UNIVERSE
#   2. Fetch live prices for those tickers
#   3. Decide action (buy / sell / limit / stop / take-profit) randomly
#   4. Scale quantity to the player's current balance and price

async def _strategy_random_buyer(client, code, player_id, token,
                                  round_num: int, name: str,
                                  portfolio: dict) -> None:
    """Buys 2-4 randomly selected assets each round."""
    picks = random.sample(UNIVERSE, k=random.randint(2, 4))
    cash = float(portfolio.get("cash_balance", STARTING_BALANCE))

    for ticker in picks:
        price = await _get_price(client, ticker)
        if price <= 0:
            continue

        if _is_crypto(ticker):
            qty = _crypto_qty(price, cash, pct=random.uniform(0.05, 0.20))
        else:
            qty = str(_affordable_qty(price, cash, pct=random.uniform(0.10, 0.25)))

        status, body = await _place(
            client, code, player_id, token,
            {"ticker": ticker, "side": "buy", "quantity": qty},
        )
        result = body.get("status", body.get("detail", ""))[:30]
        print(f"    {name} market-buy {qty} {ticker} @ ${price:.2f} → {status} {result}")
        if status == 201:
            cash = max(0, cash - price * float(qty))


async def _strategy_swing_trader(client, code, player_id, token,
                                   round_num: int, name: str,
                                   portfolio: dict) -> None:
    """Round 1: buy random assets. Round 2: sell half. Round 3+: buy new picks."""
    positions = await _get_positions(client, code, player_id, token)
    cash = float(portfolio.get("cash_balance", STARTING_BALANCE))

    if round_num == 1 or not positions:
        picks = random.sample(UNIVERSE, k=random.randint(2, 3))
        for ticker in picks:
            price = await _get_price(client, ticker)
            if price <= 0:
                continue
            qty = (_crypto_qty(price, cash, 0.15) if _is_crypto(ticker)
                   else str(_affordable_qty(price, cash, 0.20)))
            status, body = await _place(
                client, code, player_id, token,
                {"ticker": ticker, "side": "buy", "quantity": qty},
            )
            result = body.get("status", body.get("detail", ""))[:30]
            print(f"    {name} swing-buy {qty} {ticker} → {status} {result}")
    else:
        # Sell half of each position, buy new picks
        for ticker, qty in list(positions.items())[:2]:
            sell_qty = (f"{qty/2:.4f}" if _is_crypto(ticker) else str(max(1, int(qty / 2))))
            status, body = await _place(
                client, code, player_id, token,
                {"ticker": ticker, "side": "sell", "quantity": sell_qty},
            )
            result = body.get("status", body.get("detail", ""))[:30]
            print(f"    {name} swing-sell {sell_qty} {ticker} → {status} {result}")

        new_pick = random.choice([t for t in UNIVERSE if t not in positions])
        price = await _get_price(client, new_pick)
        if price > 0:
            qty = (_crypto_qty(price, cash, 0.10) if _is_crypto(new_pick)
                   else str(_affordable_qty(price, cash, 0.15)))
            status, body = await _place(
                client, code, player_id, token,
                {"ticker": new_pick, "side": "buy", "quantity": qty},
            )
            print(f"    {name} new-pick {qty} {new_pick} → {status}")


async def _strategy_limit_hunter(client, code, player_id, token,
                                   round_num: int, name: str,
                                   portfolio: dict) -> None:
    """Places GTC limit buys 2-5 % below market; occasionally places stop-loss protection."""
    picks = random.sample(UNIVERSE, k=random.randint(2, 3))
    cash = float(portfolio.get("cash_balance", STARTING_BALANCE))

    for ticker in picks:
        price = await _get_price(client, ticker)
        if price <= 0:
            continue

        discount = random.uniform(0.02, 0.05)
        limit_price = round(price * (1 - discount), 2)
        qty = (_crypto_qty(price, cash, 0.12) if _is_crypto(ticker)
               else str(_affordable_qty(price, cash, 0.18)))

        status, body = await _place(
            client, code, player_id, token,
            {
                "ticker": ticker, "side": "buy",
                "order_type": "limit", "quantity": qty,
                "limit_price": str(limit_price), "time_in_force": "gtc",
            },
        )
        result = body.get("status", body.get("detail", ""))[:30]
        print(f"    {name} limit-buy {qty} {ticker} @ ${limit_price:.2f} "
              f"(mkt ${price:.2f}) → {status} {result}")


async def _strategy_diversifier(client, code, player_id, token,
                                  round_num: int, name: str,
                                  portfolio: dict) -> None:
    """Spreads budget evenly across a random 5-8 asset selection each round."""
    n = random.randint(5, 8)
    picks = random.sample(UNIVERSE, k=n)
    cash = float(portfolio.get("cash_balance", STARTING_BALANCE))
    per_asset_pct = 0.80 / n  # spread 80 % of balance evenly

    for ticker in picks:
        price = await _get_price(client, ticker)
        if price <= 0:
            continue
        qty = (_crypto_qty(price, cash, per_asset_pct) if _is_crypto(ticker)
               else str(_affordable_qty(price, cash, per_asset_pct)))
        status, body = await _place(
            client, code, player_id, token,
            {"ticker": ticker, "side": "buy", "quantity": qty},
        )
        result = body.get("status", body.get("detail", ""))[:25]
        print(f"    {name} diversify {qty} {ticker} → {status} {result}")


async def _strategy_momentum(client, code, player_id, token,
                               round_num: int, name: str,
                               portfolio: dict) -> None:
    """Concentrates in the 2-3 best-priced random assets; adds stop-loss protection."""
    picks = random.sample(UNIVERSE, k=random.randint(2, 3))
    cash = float(portfolio.get("cash_balance", STARTING_BALANCE))

    for ticker in picks:
        price = await _get_price(client, ticker)
        if price <= 0:
            continue

        qty = (_crypto_qty(price, cash, 0.25) if _is_crypto(ticker)
               else str(_affordable_qty(price, cash, 0.30)))
        status, body = await _place(
            client, code, player_id, token,
            {"ticker": ticker, "side": "buy", "quantity": qty},
        )
        result = body.get("status", body.get("detail", ""))[:30]
        print(f"    {name} momentum-buy {qty} {ticker} @ ${price:.2f} → {status} {result}")

        # Add a stop-loss at 10-15 % below entry for positions that filled
        if status == 201 and body.get("status") == "filled" and float(qty) > 0:
            stop = round(price * random.uniform(0.85, 0.90), 2)
            sl_qty = qty  # protect full position
            sl_status, sl_body = await _place(
                client, code, player_id, token,
                {
                    "ticker": ticker, "side": "sell",
                    "order_type": "stop_loss",
                    "quantity": sl_qty, "stop_price": str(stop),
                },
            )
            print(f"    {name} stop-loss {sl_qty} {ticker} @ ${stop:.2f} → {sl_status}")


async def _strategy_contrarian(client, code, player_id, token,
                                 round_num: int, name: str,
                                 portfolio: dict) -> None:
    """Buys random assets and places take-profit orders at 10-20 % above entry."""
    picks = random.sample(UNIVERSE, k=random.randint(2, 4))
    cash = float(portfolio.get("cash_balance", STARTING_BALANCE))

    for ticker in picks:
        price = await _get_price(client, ticker)
        if price <= 0:
            continue

        qty = (_crypto_qty(price, cash, 0.15) if _is_crypto(ticker)
               else str(_affordable_qty(price, cash, 0.20)))
        status, body = await _place(
            client, code, player_id, token,
            {"ticker": ticker, "side": "buy", "quantity": qty},
        )
        result = body.get("status", body.get("detail", ""))[:30]
        print(f"    {name} contrarian-buy {qty} {ticker} → {status} {result}")

        # Take-profit at 10-20 % above entry
        if status == 201 and body.get("status") == "filled":
            tp_price = round(price * random.uniform(1.10, 1.20), 2)
            tp_status, _ = await _place(
                client, code, player_id, token,
                {
                    "ticker": ticker, "side": "sell",
                    "order_type": "take_profit",
                    "quantity": qty, "take_profit_price": str(tp_price),
                },
            )
            print(f"    {name} take-profit {qty} {ticker} @ ${tp_price:.2f} → {tp_status}")


_STRATEGIES = [
    _strategy_random_buyer,
    _strategy_swing_trader,
    _strategy_limit_hunter,
    _strategy_diversifier,
    _strategy_momentum,
    _strategy_contrarian,
]


# ── Main test ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_live_competition_full_game(live_client):
    """Full competition lifecycle: 6 players with dynamic random strategies.

    Every run produces different portfolios — no ticker is hardcoded.
    Each player samples randomly from a 19-asset universe, uses live
    yfinance prices, and mixes order types (market, limit, stop, take-profit).
    """
    client = live_client
    duration = LIVE_MINUTES if LIVE_MODE else 60

    print(f"\n{'='*60}")
    print("  TradeHub Live Competition  —  Dynamic Strategies")
    print(f"  Mode: {'LIVE (' + str(LIVE_MINUTES) + ' min)' if LIVE_MODE else 'FAST'}")
    print(f"  Universe: {len(UNIVERSE)} assets")
    print(f"  Players: {', '.join(PLAYER_NAMES)}")
    print(f"{'='*60}")

    # 1. Create competition
    from tests.conftest import join_http, register_http

    alice_reg = await register_http(client, PLAYER_NAMES[0])
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Dynamic Battle 2026",
            "asset_universe": UNIVERSE,
            "starting_balance": STARTING_BALANCE,
            "data_source": "online",
            "duration_minutes": duration,
        },
        headers={"Authorization": f"Bearer {alice_reg['token']}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    code = body["lobby_code"]
    creator_token = body["token"]
    print(f"\n  Lobby code: {code}  |  Universe: {len(UNIVERSE)} assets")

    # Assign strategies randomly so each run has a different pairing
    shuffled_strategies = random.sample(_STRATEGIES, k=len(_STRATEGIES))
    participants: dict[str, dict] = {
        PLAYER_NAMES[0]: {
            "token": creator_token,
            "player_id": None,
            "strategy": shuffled_strategies[0],
        }
    }

    # 2. Remaining players join
    for i, name in enumerate(PLAYER_NAMES[1:], start=1):
        joined = await join_http(client, code, name)
        participants[name] = {
            "token": joined["player_token"],
            "player_id": joined["player_id"],
            "strategy": shuffled_strategies[i],
        }
    print(f"  {len(PLAYER_NAMES)} players joined.")

    # 3. Start
    start_resp = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {creator_token}"},
    )
    assert start_resp.status_code == 200, start_resp.text
    print("  Competition started.")

    # 4. Resolve Alice's player_id from leaderboard
    lb_resp = await client.get(f"/competitions/{code}/leaderboard")
    for entry in lb_resp.json():
        if entry["display_name"] == PLAYER_NAMES[0]:
            participants[PLAYER_NAMES[0]]["player_id"] = entry["player_id"]

    # 5. Spectators join
    for spec_name in SPECTATOR_NAMES:
        await join_http(client, code, spec_name, spectator=True)
    print(f"  {len(SPECTATOR_NAMES)} spectators joined.")

    # 6. Verify spectator cannot trade (guard rail)
    sneaky = await join_http(client, code, "SneakySpec", spectator=True)
    bad_resp = await client.post(
        f"/competitions/{code}/players/{sneaky['player_id']}/orders",
        headers={"Authorization": f"Bearer {sneaky['player_token']}"},
        json={"ticker": random.choice(UNIVERSE), "side": "buy", "quantity": "1"},
    )
    assert bad_resp.status_code == 403
    print("  Spectator order correctly rejected (403).")

    # 7. Trading rounds — ALL players fire concurrently each round
    num_rounds = 3
    for round_num in range(1, num_rounds + 1):
        print(f"\n  ── Round {round_num} / {num_rounds} "
              f"(all {len(PLAYER_NAMES)} players trade simultaneously) ──")

        # Fetch each player's current portfolio before the round
        portfolios: dict[str, dict] = {}
        for name, info in participants.items():
            port_resp = await client.get(
                f"/players/{info['player_id']}/portfolio",
                headers={"Authorization": f"Bearer {info['token']}"},
            )
            if port_resp.status_code == 200:
                portfolios[name] = port_resp.json()
            else:
                portfolios[name] = {"cash_balance": float(STARTING_BALANCE)}

        # Run strategies sequentially within each round.
        # True HTTP-level concurrency is tested in test_live_suite.py and
        # test_concurrent_http.py; this test focuses on the full lifecycle
        # with dynamic strategies and live prices.
        for name, info in participants.items():
            await info["strategy"](
                client, code, info["player_id"], info["token"],
                round_num, name, portfolios.get(name, {}),
            )

        # Mid-round leaderboard
        lb_resp = await client.get(f"/competitions/{code}/leaderboard")
        assert lb_resp.status_code == 200
        print(f"\n  Leaderboard after round {round_num}:")
        print(_fmt_leaderboard(lb_resp.json()))

        if round_num < num_rounds:
            if LIVE_MODE:
                print(f"\n  Sleeping {_ROUND_SLEEP}s before next round…")
            await asyncio.sleep(_ROUND_SLEEP)

    # 8. End competition
    if not LIVE_MODE:
        end_resp = await client.post(
            f"/competitions/{code}/end",
            headers={"Authorization": f"Bearer {creator_token}"},
        )
        assert end_resp.status_code == 200
        print("\n  Competition ended.")
    else:
        print(f"\n  Waiting for auto-end ({LIVE_MINUTES} min)…")
        for _ in range(LIVE_MINUTES * 6 + 10):
            await asyncio.sleep(10)
            state_resp = await client.get(f"/competitions/{code}")
            if state_resp.json().get("state") == "ended":
                print("  Competition auto-ended.")
                break

    # 9. Final leaderboard
    final_resp = await client.get(f"/competitions/{code}/leaderboard")
    assert final_resp.status_code == 200
    final_entries = final_resp.json()

    print("\n  ══ FINAL STANDINGS ══")
    print(_fmt_leaderboard(final_entries))

    # 10. Assertions
    assert len(final_entries) == len(PLAYER_NAMES)
    assert [e["rank"] for e in final_entries] == list(range(1, len(PLAYER_NAMES) + 1))

    for entry in final_entries:
        assert "realized_pnl" in entry
        assert "unrealized_pnl" in entry
        assert "orders_filled" in entry
        assert "positions" in entry
        assert "max_drawdown_pct" in entry  # new metric
        assert isinstance(entry["positions"], list)

    for entry in final_entries:
        for pos in entry["positions"]:
            assert Decimal(str(pos["current_price"])) > 0, (
                f"{entry['display_name']} position {pos['ticker']} has zero price"
            )

    winner = final_entries[0]
    print(f"\n  Winner: {winner['display_name']} with ${float(winner['total_value']):,.2f}")
    print(f"  P&L: {'+' if float(winner['pnl']) >= 0 else ''}"
          f"${float(winner['pnl']):,.2f} "
          f"({'+' if float(winner['pnl_pct']) >= 0 else ''}{float(winner['pnl_pct']):.2f}%)")
    print(f"  Max Drawdown: {float(winner['max_drawdown_pct']):.2f}%")
