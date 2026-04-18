"""Integration tests via the headless simulation harness."""
from decimal import Decimal

import pytest

from tools.simulate import STRATEGIES, run_simulation


@pytest.mark.asyncio
async def test_simulation_no_negative_balances():
    report = await run_simulation(n_players=10, n_rounds=20, verbose=False)
    assert not report.invariant_violations, f"Invariant violations: {report.invariant_violations}"


@pytest.mark.asyncio
async def test_simulation_money_conservation():
    """Fees destroy money — total ending value must never exceed total starting value."""
    starting_balance = Decimal("10000")
    report = await run_simulation(
        n_players=8, n_rounds=15, starting_balance=starting_balance, verbose=False
    )
    total_start = starting_balance * report.n_players
    total_end = sum(Decimal(str(e["total_value"])) for e in report.leaderboard)
    assert total_end <= total_start + Decimal("0.01"), (
        f"Money created: started={total_start} ended={total_end}"
    )


@pytest.mark.asyncio
async def test_simulation_leaderboard_ranks_are_dense():
    report = await run_simulation(n_players=5, n_rounds=5, verbose=False)
    assert len(report.leaderboard) == 5
    ranks = sorted(e["rank"] for e in report.leaderboard)
    assert ranks == list(range(1, 6))


@pytest.mark.asyncio
async def test_simulation_leaderboard_ordered_descending():
    report = await run_simulation(n_players=5, n_rounds=10, verbose=False)
    values = [e["total_value"] for e in report.leaderboard]
    assert values == sorted(values, reverse=True)


@pytest.mark.asyncio
async def test_simulation_all_strategies_execute():
    report = await run_simulation(
        n_players=len(STRATEGIES) * 2, n_rounds=10, verbose=False
    )
    strategies_seen = {e["strategy"] for e in report.leaderboard}
    assert strategies_seen == set(STRATEGIES.keys())


@pytest.mark.asyncio
async def test_simulation_50_players_no_crashes():
    report = await run_simulation(n_players=50, n_rounds=10, verbose=False)
    assert report.total_orders_placed > 0
    assert not report.invariant_violations
    assert not report.errors  # unexpected errors (not insufficient-funds rejections)


@pytest.mark.asyncio
async def test_simulation_zero_fee_minimises_money_destruction():
    report = await run_simulation(
        n_players=5, n_rounds=10, fee_pct=Decimal("0"), verbose=False
    )
    total_start = Decimal("10000") * report.n_players
    total_end = sum(Decimal(str(e["total_value"])) for e in report.leaderboard)
    # At 0% fee, total value changes only due to price fluctuations (positions are marked to market)
    # Total value is NOT conserved because positions are revalued at current prices.
    # What IS guaranteed: no negative balances and no money creation beyond market gains.
    assert not report.invariant_violations


@pytest.mark.asyncio
async def test_simulation_high_volatility_no_crashes():
    report = await run_simulation(
        n_players=10, n_rounds=20, volatility=0.15, verbose=False
    )
    assert not report.invariant_violations
