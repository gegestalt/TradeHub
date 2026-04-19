"""Tests for competition lifecycle: end, spectator join, history, Sharpe scoring."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide
from schemas.competition import CompetitionCreate, JoinRequest
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


def make_adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(db, name: str = "Test", balance: Decimal = Decimal("10000")):
    data = CompetitionCreate(
        name=name,
        starting_balance=balance,
        asset_universe=["AAPL"],
        fee_pct=Decimal("0"),
        creator_name="Alice",
    )
    comp, alice, token = await create_competition(db, data)
    return comp, alice, token


# ── Competition end ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_competition_sets_state_ended(db):
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)
    ended = await end_competition(db, comp.lobby_code, alice)
    assert ended.state == "ended"


@pytest.mark.asyncio
async def test_end_competition_records_end_at(db):
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)
    ended = await end_competition(db, comp.lobby_code, alice)
    assert ended.end_at is not None


@pytest.mark.asyncio
async def test_end_competition_non_creator_forbidden(db):
    from fastapi import HTTPException

    data = CompetitionCreate(
        name="Test2", starting_balance=Decimal("10000"),
        asset_universe=["AAPL"], fee_pct=Decimal("0"), creator_name="Alice",
    )
    comp, alice, _ = await create_competition(db, data)
    bob, _ = await join_competition(db, comp.lobby_code, JoinRequest(display_name="Bob"))
    await start_competition(db, comp.lobby_code, alice)

    with pytest.raises(HTTPException) as exc:
        await end_competition(db, comp.lobby_code, bob)
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
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)

    spectator, _ = await join_competition(
        db, comp.lobby_code, JoinRequest(display_name="Watcher", spectator=True)
    )
    assert spectator.spectator is True


@pytest.mark.asyncio
async def test_player_cannot_join_active_competition(db):
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)

    with pytest.raises(HTTPException) as exc:
        await join_competition(db, comp.lobby_code, JoinRequest(display_name="Late Bob"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_spectator_excluded_from_leaderboard(db):
    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)

    await join_competition(
        db, comp.lobby_code, JoinRequest(display_name="Watcher", spectator=True)
    )

    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("100")))
    names = [e.display_name for e in entries]
    assert "Watcher" not in names
    assert "Alice" in names


@pytest.mark.asyncio
async def test_spectator_cannot_join_ended_competition(db):
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)
    await end_competition(db, comp.lobby_code, alice)

    with pytest.raises(HTTPException) as exc:
        await join_competition(
            db, comp.lobby_code, JoinRequest(display_name="Late Watcher", spectator=True)
        )
    assert exc.value.status_code == 400


# ── max_players enforcement ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_players_enforced(db):
    from fastapi import HTTPException

    data = CompetitionCreate(
        name="Small", starting_balance=Decimal("10000"),
        asset_universe=["AAPL"], fee_pct=Decimal("0"),
        creator_name="Alice", max_players=2,
    )
    comp, alice, _ = await create_competition(db, data)
    await join_competition(db, comp.lobby_code, JoinRequest(display_name="Bob"))

    with pytest.raises(HTTPException) as exc:
        await join_competition(db, comp.lobby_code, JoinRequest(display_name="Charlie"))
    assert exc.value.status_code == 400
    assert "full" in exc.value.detail


# ── Portfolio snapshot and history ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_task_writes_portfolio_snapshot(db):
    from sqlalchemy import select

    from models.portfolio_snapshot import PortfolioSnapshot

    comp, alice, _ = await _setup(db)
    await start_competition(db, comp.lobby_code, alice)

    # Buy some AAPL so positions_value is non-zero
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )

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
    # Set end_at to the past
    comp.end_at = datetime.utcnow() - timedelta(seconds=1)

    await _snapshot_competition(db, comp)
    assert comp.state == "ended"


@pytest.mark.asyncio
async def test_history_endpoint_returns_snapshots(client):

    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "History Test", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
        },
    )
    body = create_resp.json()
    token = body["token"]
    await client.post(
        f"/competitions/{body['lobby_code']}/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Use the competition leaderboard to get the player_id
    lb = await client.get(f"/competitions/{body['lobby_code']}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    history_resp = await client.get(
        f"/players/{player_id}/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert history_resp.status_code == 200
    data = history_resp.json()
    assert "snapshots" in data
    assert isinstance(data["snapshots"], list)
