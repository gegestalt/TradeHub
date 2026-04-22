"""Live competition simulation: 6 players, 2 spectators, online data (yfinance).

Run modes
---------
Fast (default pytest run):
    pytest tests/test_live_competition.py
    → 2 trading rounds, competition ended immediately, ~5 seconds total.

Live 30-minute run (set env var before running):
    LIVE_COMPETITION_MINUTES=30 pytest tests/test_live_competition.py -s -v
    → 3 trading rounds spread over 30 minutes with real sleep gaps,
      then auto-end by the snapshot task.

Manual script invocation:
    LIVE_COMPETITION_MINUTES=30 python -m pytest tests/test_live_competition.py -s -v
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

# ── Configuration ─────────────────────────────────────────────────────────────

LIVE_MINUTES = int(os.getenv("LIVE_COMPETITION_MINUTES", "0"))
LIVE_MODE = LIVE_MINUTES > 0

TICKERS = ["AAPL", "TSLA", "GOOGL", "MSFT"]
STARTING_BALANCE = "50000"   # generous so players don't run out of cash

PLAYER_NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank"]
SPECTATOR_NAMES = ["Watcher1", "Watcher2"]

# In fast mode, pause 1 s between rounds; in live mode spread across the full duration
_ROUND_SLEEP = (LIVE_MINUTES * 60 // 3) if LIVE_MODE else 1


# ── In-memory DB fixture (isolated from production) ───────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _buy_order(ticker: str, quantity: str) -> dict:
    return {"ticker": ticker, "side": "buy", "quantity": quantity}


def _sell_order(ticker: str, quantity: str) -> dict:
    return {"ticker": ticker, "side": "sell", "quantity": quantity}


def _limit_buy(ticker: str, quantity: str, limit_price: str) -> dict:
    return {
        "ticker": ticker,
        "side": "buy",
        "quantity": quantity,
        "order_type": "limit",
        "limit_price": limit_price,
        "time_in_force": "gtc",
    }


async def _place(client, code, player_id, token, order_body) -> dict:
    """POST an order; return the response JSON regardless of status."""
    resp = await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json=order_body,
    )
    return resp.status_code, resp.json()


async def _get_price(client, ticker: str) -> Decimal:
    """Fetch latest price from the /prices endpoint."""
    resp = await client.get(f"/prices/{ticker}")
    if resp.status_code == 200:
        return Decimal(str(resp.json().get("price", "0")))
    return Decimal("0")


def _fmt_leaderboard(entries: list[dict]) -> str:
    """Pretty-print the leaderboard for -s output."""
    lines = [
        "\n" + "═" * 90,
        f"{'RK':<4} {'PLAYER':<12} {'TOTAL VALUE':>14} {'P&L':>12} {'P&L %':>8} "
        f"{'REALIZED':>12} {'UNREALIZED':>12} {'ORDERS':>7}",
        "─" * 90,
    ]
    for e in entries:
        pnl_sign = "+" if Decimal(str(e["pnl"])) >= 0 else ""
        lines.append(
            f"{e['rank']:<4} {e['display_name']:<12} "
            f"${float(e['total_value']):>13,.2f} "
            f"{pnl_sign}${float(e['pnl']):>10,.2f} "
            f"{pnl_sign}{float(e['pnl_pct']):>7.2f}% "
            f"${float(e['realized_pnl']):>10,.2f}  "
            f"${float(e['unrealized_pnl']):>10,.2f} "
            f"{e['orders_filled']:>6}",
        )
        for pos in e.get("positions", []):
            pnl_sign = "+" if Decimal(str(pos["unrealized_pnl"])) >= 0 else ""
            lines.append(
                f"     └─ {pos['ticker']:<8} qty={float(pos['quantity']):.4f} "
                f"@ avg ${float(pos['avg_entry_price']):.2f} "
                f"| now ${float(pos['current_price']):.2f} "
                f"| value ${float(pos['market_value']):.2f} "
                f"| {pnl_sign}${float(pos['unrealized_pnl']):.2f}"
            )
    lines.append("═" * 90)
    return "\n".join(lines)


# ── Trading strategies per player ─────────────────────────────────────────────

async def _strategy_aggressive_buyer(client, code, player_id, token, round_num: int):
    """Buys large positions in AAPL and TSLA every round."""
    ticker = "AAPL" if round_num % 2 == 0 else "TSLA"
    status, body = await _place(client, code, player_id, token, _buy_order(ticker, "5"))
    print(f"    Alice bought 5 {ticker} → {status} {body.get('status', body.get('detail', ''))}")


async def _strategy_value_trader(client, code, player_id, token, round_num: int):
    """Buys GOOGL and MSFT with limit orders slightly below market."""
    ticker = random.choice(["GOOGL", "MSFT"])
    # Market buy on round 1, limit on subsequent
    if round_num == 1:
        status, body = await _place(client, code, player_id, token, _buy_order(ticker, "3"))
        result = body.get("status", body.get("detail", ""))
        print(f"    Bob market-bought 3 {ticker} → {status} {result}")
    else:
        price_resp = await client.get(f"/prices/{ticker}")
        if price_resp.status_code == 200:
            market_price = float(price_resp.json().get("price", 200))
            limit = round(market_price * 0.97, 2)  # 3% below market → GTC limit
            status, body = await _place(
                client, code, player_id, token,
                _limit_buy(ticker, "2", str(limit)),
            )
            print(f"    Bob placed GTC limit buy 2 {ticker} @ ${limit} → {status}")


async def _strategy_swing_trader(client, code, player_id, token, round_num: int):
    """Buys round 1, sells round 2, buys again round 3."""
    if round_num == 1:
        status, body = await _place(client, code, player_id, token, _buy_order("TSLA", "4"))
        print(f"    Charlie bought 4 TSLA → {status} {body.get('status', body.get('detail', ''))}")
    elif round_num == 2:
        status, body = await _place(client, code, player_id, token, _sell_order("TSLA", "2"))
        print(f"    Charlie sold 2 TSLA → {status} {body.get('status', body.get('detail', ''))}")
    else:
        status, body = await _place(client, code, player_id, token, _buy_order("GOOGL", "3"))
        print(f"    Charlie bought 3 GOOGL → {status} {body.get('status', body.get('detail', ''))}")


async def _strategy_diversifier(client, code, player_id, token, round_num: int):
    """Spreads across all 4 tickers each round."""
    for ticker in TICKERS:
        status, body = await _place(client, code, player_id, token, _buy_order(ticker, "1"))
        result = body.get("status", body.get("detail", ""))
        print(f"    Diana bought 1 {ticker} → {status} {result}")


async def _strategy_momentum(client, code, player_id, token, round_num: int):
    """Buys AAPL and MSFT heavily."""
    for ticker in ["AAPL", "MSFT"]:
        status, body = await _place(client, code, player_id, token, _buy_order(ticker, "3"))
        print(f"    Eve bought 3 {ticker} → {status} {body.get('status', body.get('detail', ''))}")


async def _strategy_contrarian(client, code, player_id, token, round_num: int):
    """Buys what others don't; tries a mix of GOOGL and MSFT."""
    ticker = "MSFT" if round_num % 2 == 0 else "GOOGL"
    qty = str(random.choice([2, 3, 4]))
    status, body = await _place(client, code, player_id, token, _buy_order(ticker, qty))
    result = body.get("status", body.get("detail", ""))
    print(f"    Frank bought {qty} {ticker} → {status} {result}")


_STRATEGIES = [
    _strategy_aggressive_buyer,
    _strategy_value_trader,
    _strategy_swing_trader,
    _strategy_diversifier,
    _strategy_momentum,
    _strategy_contrarian,
]


# ── Main test ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_live_competition_full_game(live_client):
    """
    Full competition lifecycle with 6 players and live yfinance prices.

    Fast mode   (default): completes in ~5 s — good for CI.
    Live mode   (env var):  LIVE_COMPETITION_MINUTES=30 for a real 30-minute session.
    """
    client = live_client
    duration = LIVE_MINUTES if LIVE_MODE else 60  # 1-hour duration, ended manually in fast mode

    print(f"\n{'='*60}")
    print("  TradeHub Live Competition")
    print(f"  Mode: {'LIVE (' + str(LIVE_MINUTES) + ' min)' if LIVE_MODE else 'FAST (manual end)'}")
    print(f"  Tickers: {', '.join(TICKERS)}")
    print(f"  Players: {', '.join(PLAYER_NAMES)}")
    print(f"{'='*60}")

    # ── 1. Create competition ──────────────────────────────────────────────────
    from tests.conftest import join_http, register_http
    alice_reg = await register_http(client, PLAYER_NAMES[0])
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Live Battle 2026",
            "asset_universe": TICKERS,
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
    print(f"\n  Lobby code: {code}")

    # Store per-player state: {name: {token, player_id, strategy}}
    participants: dict[str, dict] = {
        PLAYER_NAMES[0]: {"token": creator_token, "player_id": None, "strategy": _STRATEGIES[0]}
    }

    # ── 2. Remaining 5 players join ────────────────────────────────────────────
    for i, name in enumerate(PLAYER_NAMES[1:], start=1):
        joined = await join_http(client, code, name)
        participants[name] = {
            "token": joined["player_token"],
            "player_id": joined["player_id"],
            "strategy": _STRATEGIES[i],
        }
    print(f"  {len(PLAYER_NAMES)} players joined.")

    # ── 3. Start competition ───────────────────────────────────────────────────
    start_resp = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {creator_token}"},
    )
    assert start_resp.status_code == 200, start_resp.text
    print("  Competition started.")

    # ── 4. Get creator's player_id from leaderboard ────────────────────────────
    lb_resp = await client.get(f"/competitions/{code}/leaderboard")
    assert lb_resp.status_code == 200
    for entry in lb_resp.json():
        if entry["display_name"] == PLAYER_NAMES[0]:
            participants[PLAYER_NAMES[0]]["player_id"] = entry["player_id"]

    # ── 5. Spectators join after start ────────────────────────────────────────
    for spec_name in SPECTATOR_NAMES:
        spec = await join_http(client, code, spec_name, spectator=True)
        assert spec["player_token"]
    print(f"  {len(SPECTATOR_NAMES)} spectators joined.")

    # ── 6. Verify spectator cannot trade ─────────────────────────────────────
    sneaky = await join_http(client, code, "SneakySpec", spectator=True)
    bad_resp = await client.post(
        f"/competitions/{code}/players/{sneaky['player_id']}/orders",
        headers={"Authorization": f"Bearer {sneaky['player_token']}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert bad_resp.status_code == 403
    print("  Spectator order correctly rejected (403).")

    # ── 7. Trading rounds ─────────────────────────────────────────────────────
    num_rounds = 3
    for round_num in range(1, num_rounds + 1):
        print(f"\n  ── Round {round_num} ──")
        for _name, info in participants.items():
            await info["strategy"](
                client, code, info["player_id"], info["token"], round_num
            )

        # Mid-round leaderboard snapshot
        lb_resp = await client.get(f"/competitions/{code}/leaderboard")
        assert lb_resp.status_code == 200
        print(f"\n  Leaderboard after round {round_num}:")
        print(_fmt_leaderboard(lb_resp.json()))

        if round_num < num_rounds:
            if LIVE_MODE:
                print(f"\n  Sleeping {_ROUND_SLEEP}s before next round…")
            await asyncio.sleep(_ROUND_SLEEP)

    # ── 8. End competition ────────────────────────────────────────────────────
    if not LIVE_MODE:
        end_resp = await client.post(
            f"/competitions/{code}/end",
            headers={"Authorization": f"Bearer {creator_token}"},
        )
        assert end_resp.status_code == 200
        print("\n  Competition ended.")
    else:
        # In live mode the snapshot task auto-ends when end_at passes
        print(f"\n  Waiting for competition to auto-end ({LIVE_MINUTES} min)…")
        # Poll until ended
        for _ in range(LIVE_MINUTES * 6 + 10):  # check every 10 s
            await asyncio.sleep(10)
            state_resp = await client.get(f"/competitions/{code}")
            if state_resp.json().get("state") == "ended":
                print("  Competition auto-ended.")
                break

    # ── 9. Final leaderboard ──────────────────────────────────────────────────
    final_resp = await client.get(f"/competitions/{code}/leaderboard")
    assert final_resp.status_code == 200
    final_entries = final_resp.json()

    print("\n  ══ FINAL LEADERBOARD ══")
    print(_fmt_leaderboard(final_entries))

    # ── 10. Assertions ────────────────────────────────────────────────────────
    # Exactly 6 entries (spectators excluded)
    assert len(final_entries) == len(PLAYER_NAMES), (
        f"Expected {len(PLAYER_NAMES)} entries, got {len(final_entries)}"
    )

    # Ranks are 1-based and sequential
    assert [e["rank"] for e in final_entries] == list(range(1, len(PLAYER_NAMES) + 1))

    # All entries have required fields populated
    for entry in final_entries:
        assert "realized_pnl" in entry
        assert "unrealized_pnl" in entry
        assert "orders_filled" in entry
        assert "positions" in entry
        assert isinstance(entry["positions"], list)

    # Every position has a positive current_price (yfinance returned data)
    for entry in final_entries:
        for pos in entry["positions"]:
            assert Decimal(str(pos["current_price"])) > 0, (
                f"{entry['display_name']} position {pos['ticker']} has zero price"
            )

    # Winner has highest total_value
    winner = final_entries[0]
    print(f"\n  Winner: {winner['display_name']} with ${float(winner['total_value']):,.2f}")
    print(f"  P&L: {'+' if float(winner['pnl']) >= 0 else ''}${float(winner['pnl']):,.2f} "
          f"({'+' if float(winner['pnl_pct']) >= 0 else ''}{float(winner['pnl_pct']):.2f}%)")
