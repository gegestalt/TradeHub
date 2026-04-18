#!/usr/bin/env python3
"""
Headless competition simulation — acts as a stress-test / integration harness.

Usage (from backend/):
    uv run python tools/simulate.py
    uv run python tools/simulate.py --players 50 --rounds 100 --seed 1337
    uv run python tools/simulate.py --players 10 --rounds 20 --verbose
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import models  # noqa: F401 — registers ORM models
from data_adapters.mock import MockDataAdapter
from database import Base
from models.enums import CompetitionState, OrderSide
from models.position import Position
from schemas.competition import CompetitionCreate, JoinRequest
from schemas.order import OrderCreate
from services.competition import create_competition, get_leaderboard, join_competition, start_competition
from services.order_engine import place_order

_DP2 = Decimal("0.01")

TICKERS = ["AAPL", "TSLA", "BTC-USD", "ETH-USD", "GOOGL"]

# Strategy type alias
StrategyFn = Callable[..., "asyncio.coroutine"]


# ── Strategies ────────────────────────────────────────────────────────────────

async def strategy_buy_hold(player, competition, adapter, round_num, rng, db) -> list[OrderCreate]:
    """Buy one asset at round 0 and never trade again."""
    if round_num != 0:
        return []
    ticker = rng.choice(competition.asset_universe)
    price = adapter.get_price(ticker)
    qty = (player.cash_balance * Decimal("0.9") / price).quantize(_DP2, rounding=ROUND_HALF_UP)
    return [OrderCreate(ticker=ticker, side=OrderSide.buy, quantity=qty)] if qty > Decimal("0") else []


async def strategy_frequent_trader(player, competition, adapter, round_num, rng, db) -> list[OrderCreate]:
    """Random buy/sell on most rounds — high turnover."""
    orders: list[OrderCreate] = []

    if rng.random() < 0.65 and player.cash_balance > Decimal("10"):
        ticker = rng.choice(competition.asset_universe)
        price = adapter.get_price(ticker)
        spend = player.cash_balance * Decimal(str(round(rng.uniform(0.05, 0.25), 2)))
        qty = (spend / price).quantize(_DP2, rounding=ROUND_HALF_UP)
        if qty > Decimal("0"):
            orders.append(OrderCreate(ticker=ticker, side=OrderSide.buy, quantity=qty))

    if rng.random() < 0.45:
        result = await db.execute(
            select(Position).where(Position.player_id == player.id, Position.quantity > 0)
        )
        long_positions = result.scalars().all()
        if long_positions:
            pos = rng.choice(long_positions)
            sell_qty = (pos.quantity * Decimal(str(round(rng.uniform(0.3, 1.0), 2)))).quantize(
                _DP2, rounding=ROUND_HALF_UP
            )
            if sell_qty > Decimal("0"):
                orders.append(OrderCreate(ticker=pos.ticker, side=OrderSide.sell, quantity=sell_qty))

    return orders


async def strategy_diversifier(player, competition, adapter, round_num, rng, db) -> list[OrderCreate]:
    """Spread capital equally across all assets on round 0."""
    if round_num != 0:
        return []
    n = len(competition.asset_universe)
    per_ticker = player.cash_balance * Decimal("0.9") / n
    orders = []
    for ticker in competition.asset_universe:
        price = adapter.get_price(ticker)
        qty = (per_ticker / price).quantize(_DP2, rounding=ROUND_HALF_UP)
        if qty > Decimal("0"):
            orders.append(OrderCreate(ticker=ticker, side=OrderSide.buy, quantity=qty))
    return orders


async def strategy_momentum(player, competition, adapter, round_num, rng, db) -> list[OrderCreate]:
    """Buy recent winners, sell recent losers."""
    if round_num < 2:
        return []
    orders = []
    for ticker in competition.asset_universe:
        history = adapter.price_history(ticker)
        if len(history) < 2:
            continue
        _, prev = history[-2]
        _, curr = history[-1]
        if curr > prev and player.cash_balance > Decimal("50"):
            price = adapter.get_price(ticker)
            qty = (player.cash_balance * Decimal("0.15") / price).quantize(_DP2, rounding=ROUND_HALF_UP)
            if qty > Decimal("0"):
                orders.append(OrderCreate(ticker=ticker, side=OrderSide.buy, quantity=qty))
        elif curr < prev:
            result = await db.execute(
                select(Position).where(Position.player_id == player.id, Position.ticker == ticker)
            )
            pos = result.scalar_one_or_none()
            if pos and pos.quantity > Decimal("0"):
                sell_qty = (pos.quantity * Decimal("0.5")).quantize(_DP2, rounding=ROUND_HALF_UP)
                if sell_qty > Decimal("0"):
                    orders.append(OrderCreate(ticker=ticker, side=OrderSide.sell, quantity=sell_qty))
    return orders


async def strategy_random(player, competition, adapter, round_num, rng, db) -> list[OrderCreate]:
    """Completely random buy or sell with 50% skip rate."""
    if rng.random() < 0.5:
        return []
    ticker = rng.choice(competition.asset_universe)
    side = OrderSide.buy if rng.random() < 0.6 else OrderSide.sell

    if side == OrderSide.sell:
        result = await db.execute(
            select(Position).where(Position.player_id == player.id, Position.ticker == ticker)
        )
        pos = result.scalar_one_or_none()
        if not pos or pos.quantity <= Decimal("0.01"):
            return []
        qty = (pos.quantity * Decimal(str(round(rng.uniform(0.1, 1.0), 2)))).quantize(
            _DP2, rounding=ROUND_HALF_UP
        )
    else:
        if player.cash_balance < Decimal("1"):
            return []
        price = adapter.get_price(ticker)
        spend = player.cash_balance * Decimal(str(round(rng.uniform(0.01, 0.2), 2)))
        qty = (spend / price).quantize(_DP2, rounding=ROUND_HALF_UP)

    return [OrderCreate(ticker=ticker, side=side, quantity=qty)] if qty > Decimal("0") else []


STRATEGIES: dict[str, StrategyFn] = {
    "buy_hold": strategy_buy_hold,
    "frequent_trader": strategy_frequent_trader,
    "diversifier": strategy_diversifier,
    "momentum": strategy_momentum,
    "random": strategy_random,
}


# ── Report ────────────────────────────────────────────────────────────────────

@dataclass
class PlayerStats:
    name: str
    strategy: str
    orders_placed: int = 0
    orders_filled: int = 0
    orders_rejected: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class SimulationReport:
    n_players: int
    n_rounds: int
    total_orders_placed: int
    total_orders_filled: int
    total_orders_rejected: int
    starting_balance: Decimal
    leaderboard: list[dict]
    player_stats: list[PlayerStats]
    elapsed_seconds: float
    errors: list[str]
    invariant_violations: list[str]

    @property
    def fill_rate(self) -> float:
        return self.total_orders_filled / self.total_orders_placed if self.total_orders_placed else 0.0

    def print(self) -> None:
        sep = "=" * 68
        print(f"\n{sep}")
        print(f"  SIMULATION  ({self.n_players} players · {self.n_rounds} rounds)")
        print(sep)
        print(f"  Duration       {self.elapsed_seconds:.2f}s")
        print(f"  Orders         {self.total_orders_placed} placed  "
              f"{self.total_orders_filled} filled  "
              f"{self.total_orders_rejected} rejected  "
              f"({self.fill_rate * 100:.1f}% fill rate)")

        total_start = self.starting_balance * self.n_players
        total_end = sum(Decimal(str(e["total_value"])) for e in self.leaderboard)
        fees_burned = total_start - total_end
        print(f"  Fees burned    ${fees_burned:,.4f}")

        print(f"\n  {'Rank':<5} {'Player':<22} {'Strategy':<16} {'Total':>10} {'PnL':>9} {'PnL%':>6}")
        print("  " + "-" * 72)
        for e in self.leaderboard:
            print(
                f"  {e['rank']:<5} {e['display_name']:<22} {e['strategy']:<16} "
                f"${e['total_value']:>9,.2f} ${e['pnl']:>8,.2f} {e['pnl_pct']:>5.1f}%"
            )

        if self.invariant_violations:
            print(f"\n  !! INVARIANT VIOLATIONS ({len(self.invariant_violations)}):")
            for v in self.invariant_violations:
                print(f"     {v}")

        if self.errors:
            print(f"\n  UNEXPECTED ERRORS ({len(self.errors)}):")
            for err in self.errors[:5]:
                print(f"     {err}")

        print(sep + "\n")


# ── Core runner ───────────────────────────────────────────────────────────────

async def run_simulation(
    n_players: int = 20,
    n_rounds: int = 30,
    starting_balance: Decimal = Decimal("10000"),
    fee_pct: Decimal = Decimal("0.001"),
    seed: int = 42,
    tickers: list[str] | None = None,
    volatility: float = 0.02,
    verbose: bool = True,
    db_url: str = "sqlite+aiosqlite:///:memory:",
) -> SimulationReport:
    if tickers is None:
        tickers = TICKERS

    rng = random.Random(seed)
    strategy_names = list(STRATEGIES.keys())

    test_engine = create_async_engine(db_url)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    adapter = MockDataAdapter(tickers, seed=seed)
    errors: list[str] = []
    counters = {"placed": 0, "filled": 0, "rejected": 0}
    t0 = time.perf_counter()

    async with session_factory() as db:
        comp_data = CompetitionCreate(
            name="Simulation",
            starting_balance=starting_balance,
            asset_universe=tickers,
            fee_pct=fee_pct,
            creator_name="Host",
        )
        competition, creator, _ = await create_competition(db, comp_data)

        players = [creator]
        assigned: dict[str, tuple[str, StrategyFn]] = {
            creator.id: ("buy_hold", strategy_buy_hold)
        }
        player_stats: list[PlayerStats] = [PlayerStats(name="Host", strategy="buy_hold")]

        for i in range(n_players - 1):
            sname = strategy_names[i % len(strategy_names)]
            player, _ = await join_competition(
                db, competition.lobby_code, JoinRequest(display_name=f"P{i + 1}-{sname[:3]}")
            )
            players.append(player)
            assigned[player.id] = (sname, STRATEGIES[sname])
            player_stats.append(PlayerStats(name=f"P{i + 1}-{sname[:3]}", strategy=sname))

        stats_by_id = {p.id: s for p, s in zip(players, player_stats)}
        await start_competition(db, competition.lobby_code, creator)

        if verbose:
            print(
                f"\nSimulation starting: {n_players} players · {n_rounds} rounds · "
                f"{len(tickers)} tickers · ${starting_balance:,.0f} balance · "
                f"{float(fee_pct) * 100:.2f}% fee"
            )

        for round_num in range(n_rounds):
            adapter.tick(volatility=volatility)

            async def run_player(player, stats: PlayerStats):
                sname, strategy_fn = assigned[player.id]
                try:
                    orders = await strategy_fn(player, competition, adapter, round_num, rng, db)
                    for order_data in orders:
                        counters["placed"] += 1
                        stats.orders_placed += 1
                        try:
                            await place_order(db, player, competition, order_data, adapter)
                            counters["filled"] += 1
                            stats.orders_filled += 1
                        except Exception as exc:
                            counters["rejected"] += 1
                            stats.orders_rejected += 1
                            msg = str(exc)
                            # Expected rejections (insufficient funds/position) are not errors
                            if not any(k in msg for k in ("Insufficient", "400", "not active")):
                                errors.append(f"Round {round_num} {stats.name}: {msg}")
                except Exception as exc:
                    errors.append(f"Round {round_num} strategy crash {stats.name}: {exc}")

            await asyncio.gather(*[run_player(p, stats_by_id[p.id]) for p in players])

            if verbose and (round_num + 1) % max(1, n_rounds // 5) == 0:
                print(f"  Round {round_num + 1:>3}/{n_rounds} — {counters['filled']} fills")

        leaderboard_entries = await get_leaderboard(db, competition.lobby_code, adapter)

        # Invariant checks
        invariant_violations: list[str] = []
        for p in players:
            if p.cash_balance < Decimal("0"):
                invariant_violations.append(
                    f"NEGATIVE BALANCE: {p.display_name} = {p.cash_balance}"
                )

        total_start = starting_balance * len(players)
        total_end = sum(e.total_value for e in leaderboard_entries)
        if total_end > total_start + Decimal("0.01"):
            invariant_violations.append(
                f"MONEY CREATION: started={total_start} ended={total_end} delta={total_end - total_start}"
            )

        leaderboard = []
        for e in leaderboard_entries:
            leaderboard.append({
                "rank": e.rank,
                "display_name": e.display_name,
                "strategy": assigned[e.player_id][0],
                "total_value": float(e.total_value),
                "cash_balance": float(e.cash_balance),
                "positions_value": float(e.positions_value),
                "pnl": float(e.pnl),
                "pnl_pct": float(e.pnl_pct),
            })

    await test_engine.dispose()

    return SimulationReport(
        n_players=n_players,
        n_rounds=n_rounds,
        total_orders_placed=counters["placed"],
        total_orders_filled=counters["filled"],
        total_orders_rejected=counters["rejected"],
        starting_balance=starting_balance,
        leaderboard=leaderboard,
        player_stats=player_stats,
        elapsed_seconds=time.perf_counter() - t0,
        errors=errors,
        invariant_violations=invariant_violations,
    )


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeHub headless competition simulation")
    parser.add_argument("--players", type=int, default=20)
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--balance", type=float, default=10_000)
    parser.add_argument("--fee", type=float, default=0.001)
    parser.add_argument("--volatility", type=float, default=0.02)
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    report = asyncio.run(
        run_simulation(
            n_players=args.players,
            n_rounds=args.rounds,
            seed=args.seed,
            starting_balance=Decimal(str(args.balance)),
            fee_pct=Decimal(str(args.fee)),
            volatility=args.volatility,
            verbose=args.verbose,
        )
    )
    report.print()
    sys.exit(1 if (report.errors or report.invariant_violations) else 0)
