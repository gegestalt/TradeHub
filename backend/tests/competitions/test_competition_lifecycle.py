"""Tests for competition lifecycle: end, spectator join, history, Sharpe scoring."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide
from schemas.competition import CompetitionCreate
from schemas.order import OrderCreate
from services.competition import (
    create_competition,
    end_competition,
    get_leaderboard,
    join_competition,
    start_competition,
)
from services.order_engine import place_order
from services.snapshot_task import _snapshot_competition
from tests.conftest import create_lobby_http, make_user


def make_adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(db, name: str = "Test", balance: Decimal = Decimal("10000")):
    alice, _ = await make_user(db, "Alice")
    data = CompetitionCreate(
        name=name,
        starting_balance=balance,
        asset_universe=["AAPL"],
        fee_pct=Decimal("0"),
    )
    comp, alice_player, token = await create_competition(db, data, alice)
    return comp, alice_player, token


# ── Competition end ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_competition_sets_state_ended(db):
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)
    ended = await end_competition(db, comp.lobby_code, alice)
    assert ended.state == "ended"


@pytest.mark.asyncio
async def test_end_competition_non_creator_forbidden(db):
    from fastapi import HTTPException

    alice, _ = await make_user(db, "Alice2")
    bob, _ = await make_user(db, "Bob")
    data = CompetitionCreate(
        name="Test2", starting_balance=Decimal("10000"),
        asset_universe=["AAPL"], fee_pct=Decimal("0"),
    )
    comp, alice_player, _ = await create_competition(db, data, alice)
    bob_player, _ = await join_competition(db, comp.lobby_code, bob)
    await start_competition(db, comp.lobby_code, alice_player)

    with pytest.raises(HTTPException) as exc:
        await end_competition(db, comp.lobby_code, bob_player)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_end_competition_already_ended_raises(db):
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)
    await end_competition(db, comp.lobby_code, alice)

    with pytest.raises(HTTPException) as exc:
        await end_competition(db, comp.lobby_code, alice)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cannot_end_lobby_state_competition(db):
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await end_competition(db, comp.lobby_code, alice)
    assert exc.value.status_code == 400


# ── Spectator join ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spectator_can_join_active_competition(db):
    watcher, _ = await make_user(db, "Watcher")
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)

    spectator, _ = await join_competition(db, comp.lobby_code, watcher, spectator=True)
    assert spectator.spectator is True


@pytest.mark.asyncio
async def test_player_cannot_join_active_competition(db):
    from fastapi import HTTPException

    late, _ = await make_user(db, "Late Bob")
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)

    with pytest.raises(HTTPException) as exc:
        await join_competition(db, comp.lobby_code, late)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_spectator_excluded_from_leaderboard(db):
    watcher, _ = await make_user(db, "Watcher")
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)

    await join_competition(db, comp.lobby_code, watcher, spectator=True)

    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("100")))
    names = [e.display_name for e in entries]
    assert "Watcher" not in names
    assert "Alice" in names


@pytest.mark.asyncio
async def test_spectator_cannot_join_ended_competition(db):
    from fastapi import HTTPException

    late, _ = await make_user(db, "Late Watcher")
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)
    await end_competition(db, comp.lobby_code, alice)

    with pytest.raises(HTTPException) as exc:
        await join_competition(db, comp.lobby_code, late, spectator=True)
    assert exc.value.status_code == 400


# ── max_players enforcement ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_players_enforced(db):
    from fastapi import HTTPException

    alice, _ = await make_user(db, "AliceMax")
    bob, _ = await make_user(db, "Bob")
    charlie, _ = await make_user(db, "Charlie")
    data = CompetitionCreate(
        name="Small", starting_balance=Decimal("10000"),
        asset_universe=["AAPL"], fee_pct=Decimal("0"), max_players=2,
    )
    comp, _, _ = await create_competition(db, data, alice)
    await join_competition(db, comp.lobby_code, bob)

    with pytest.raises(HTTPException) as exc:
        await join_competition(db, comp.lobby_code, charlie)
    assert exc.value.status_code == 400
    assert "full" in exc.value.detail


# ── Portfolio snapshot and history ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_task_writes_portfolio_snapshot(db):
    from unittest.mock import patch

    from sqlalchemy import select

    from models.portfolio_snapshot import PortfolioSnapshot

    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)

    stub = make_adapter(Decimal("100"))

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        stub,
    )

    with patch("services.snapshot_task.get_adapter", return_value=stub):
        await _snapshot_competition(db, comp)

    result = await db.execute(
        select(PortfolioSnapshot).where(PortfolioSnapshot.player_id == alice.id)
    )
    snapshots = result.scalars().all()
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.positions_value == Decimal("500")  # 5 * 100
    assert snap.cash_balance == Decimal("9500")    # 10000 - 500
    assert snap.total_value == Decimal("10000")
    assert snap.pnl == Decimal("0")


@pytest.mark.asyncio
async def test_snapshot_task_auto_ends_expired_competition(db):
    from datetime import datetime, timedelta

    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)
    comp.end_at = datetime.utcnow() - timedelta(seconds=1)

    await _snapshot_competition(db, comp)
    assert comp.state == "ended"


@pytest.mark.asyncio
async def test_history_endpoint_returns_snapshots(client):
    ctx = await create_lobby_http(
        client, "Alice",
        asset_universe=["AAPL"], starting_balance="10000",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    history_resp = await client.get(
        f"/players/{player_id}/history",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    assert history_resp.status_code == 200
    data = history_resp.json()
    assert "snapshots" in data
    assert isinstance(data["snapshots"], list)
