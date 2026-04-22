"""Tests for the snapshot mechanism.

The project spec states: "Prices are snapshotted periodically and stored as
PriceSnapshot records. Order fills reference these snapshots, not live prices,
to ensure consistency and support replay."

This file tests:
  1. _snapshot_competition — correct PriceSnapshot and PortfolioSnapshot creation
  2. Auto-end behavior when end_at has passed
  3. Mock price advancement on each snapshot cycle
  4. price_service — snapshot-backed price queries
  5. GET /players/{id}/history — the history HTTP endpoint
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from data_adapters.mock import MockDataAdapter, _registry
from models.enums import CompetitionState, DataSource, PriceSource
from models.portfolio_snapshot import PortfolioSnapshot
from models.price_snapshot import PriceSnapshot
from schemas.competition import CompetitionCreate, JoinRequest
from services.competition import create_competition, join_competition, start_competition
from services.price_service import (
    get_latest_snapshot_price,
    get_price_at,
    get_price_timeline,
)
from services.snapshot_task import _snapshot_competition

TICKERS = ["AAPL", "TSLA"]
STARTING_BALANCE = Decimal("10000")


def _competition_data(**overrides) -> CompetitionCreate:
    defaults = dict(
        name="Snapshot Test",
        creator_name="Alice",
        starting_balance=STARTING_BALANCE,
        asset_universe=TICKERS,
        data_source=DataSource.mock,
    )
    defaults.update(overrides)
    return CompetitionCreate(**defaults)


@pytest.fixture(autouse=True)
def clear_mock_registry():
    _clear_registry()
    yield
    _clear_registry()


def _clear_registry():
    import data_adapters.mock as _m
    with _m._lock:
        _registry.clear()


# ── PriceSnapshot creation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_records_price_for_every_ticker(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    await _snapshot_competition(db, comp)
    await db.flush()

    result = await db.execute(
        select(PriceSnapshot).where(PriceSnapshot.competition_id == comp.id)
    )
    snapshots = result.scalars().all()
    recorded_tickers = {s.ticker for s in snapshots}

    assert recorded_tickers == set(TICKERS)
    for snap in snapshots:
        assert snap.price > Decimal("0")
        assert snap.source == PriceSource.mock
        assert snap.competition_id == comp.id


@pytest.mark.asyncio
async def test_snapshot_recorded_at_is_within_test_window(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    before = datetime.utcnow()
    await _snapshot_competition(db, comp)
    await db.flush()
    after = datetime.utcnow()

    result = await db.execute(
        select(PriceSnapshot).where(PriceSnapshot.competition_id == comp.id)
    )
    for snap in result.scalars().all():
        assert before <= snap.recorded_at <= after, (
            f"Snapshot recorded_at {snap.recorded_at} outside [{before}, {after}]"
        )


@pytest.mark.asyncio
async def test_multiple_snapshot_cycles_accumulate_records(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    await _snapshot_competition(db, comp)
    await _snapshot_competition(db, comp)
    await db.flush()

    result = await db.execute(
        select(func.count()).where(PriceSnapshot.competition_id == comp.id)
    )
    count = result.scalar_one()
    assert count == len(TICKERS) * 2


# ── PortfolioSnapshot creation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_creates_portfolio_snapshot_for_each_player(db):
    comp, alice, _ = await create_competition(db, _competition_data())
    bob, _ = await join_competition(db, comp.lobby_code, JoinRequest(display_name="Bob"))
    await start_competition(db, comp.lobby_code, alice)
    await db.flush()

    await _snapshot_competition(db, comp)
    await db.flush()

    result = await db.execute(
        select(PortfolioSnapshot).where(PortfolioSnapshot.competition_id == comp.id)
    )
    snapshots = result.scalars().all()
    snapshotted_players = {s.player_id for s in snapshots}

    assert alice.id in snapshotted_players
    assert bob.id in snapshotted_players
    assert len(snapshotted_players) == 2


@pytest.mark.asyncio
async def test_initial_snapshot_has_zero_positions_and_zero_pnl(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    await _snapshot_competition(db, comp)
    await db.flush()

    result = await db.execute(
        select(PortfolioSnapshot).where(
            PortfolioSnapshot.competition_id == comp.id,
            PortfolioSnapshot.player_id == creator.id,
        )
    )
    snap = result.scalar_one()

    assert snap.total_value == STARTING_BALANCE
    assert snap.cash_balance == STARTING_BALANCE
    assert snap.positions_value == Decimal("0")
    assert snap.pnl == Decimal("0")


@pytest.mark.asyncio
async def test_pnl_equals_total_value_minus_starting_balance(db):
    """Invariant: pnl = total_value - starting_balance at every snapshot."""
    comp, creator, _ = await create_competition(db, _competition_data())
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    from data_adapters.factory import get_adapter
    from models.enums import OrderSide, OrderType
    from schemas.order import OrderCreate
    from services.order_engine import place_order

    adapter = get_adapter(comp)
    await place_order(
        db, creator, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy, order_type=OrderType.market, quantity=Decimal("5")
        ),
        adapter,
    )
    await db.flush()

    MockDataAdapter.for_competition(comp.id, TICKERS).tick()
    await _snapshot_competition(db, comp)
    await db.flush()

    result = await db.execute(
        select(PortfolioSnapshot).where(
            PortfolioSnapshot.competition_id == comp.id,
            PortfolioSnapshot.player_id == creator.id,
        )
    )
    snap = result.scalar_one()

    assert snap.pnl == snap.total_value - STARTING_BALANCE, (
        f"pnl {snap.pnl} != total_value {snap.total_value} - starting {STARTING_BALANCE}"
    )


@pytest.mark.asyncio
async def test_snapshot_positions_value_reflects_open_positions(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    from data_adapters.factory import get_adapter
    from models.enums import OrderSide, OrderType
    from schemas.order import OrderCreate
    from services.order_engine import place_order

    adapter = get_adapter(comp)
    await place_order(
        db, creator, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy, order_type=OrderType.market, quantity=Decimal("10")
        ),
        adapter,
    )
    await db.flush()

    await _snapshot_competition(db, comp)
    await db.flush()

    result = await db.execute(
        select(PortfolioSnapshot).where(
            PortfolioSnapshot.competition_id == comp.id,
            PortfolioSnapshot.player_id == creator.id,
        )
    )
    snap = result.scalar_one()

    assert snap.positions_value > Decimal("0")
    assert snap.total_value == snap.cash_balance + snap.positions_value


@pytest.mark.asyncio
async def test_spectator_is_excluded_from_portfolio_snapshot(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    spectator, _ = await join_competition(
        db, comp.lobby_code, JoinRequest(display_name="Watcher", spectator=True)
    )
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    await _snapshot_competition(db, comp)
    await db.flush()

    result = await db.execute(
        select(PortfolioSnapshot).where(PortfolioSnapshot.player_id == spectator.id)
    )
    assert result.scalar_one_or_none() is None


# ── Auto-end ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_auto_ends_competition_when_end_at_has_passed(db):
    past = datetime.utcnow() - timedelta(seconds=1)
    comp, creator, _ = await create_competition(db, _competition_data(end_at=past))
    await start_competition(db, comp.lobby_code, creator)
    comp.end_at = past
    await db.flush()

    await _snapshot_competition(db, comp)

    assert comp.state == CompetitionState.ended


@pytest.mark.asyncio
async def test_auto_ended_competition_writes_no_price_snapshots(db):
    past = datetime.utcnow() - timedelta(seconds=1)
    comp, creator, _ = await create_competition(db, _competition_data(end_at=past))
    await start_competition(db, comp.lobby_code, creator)
    comp.end_at = past
    await db.flush()

    await _snapshot_competition(db, comp)
    await db.flush()

    result = await db.execute(
        select(func.count()).where(PriceSnapshot.competition_id == comp.id)
    )
    assert result.scalar_one() == 0


@pytest.mark.asyncio
async def test_competition_with_future_end_at_stays_active(db):
    future = datetime.utcnow() + timedelta(hours=1)
    comp, creator, _ = await create_competition(db, _competition_data(end_at=future))
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    await _snapshot_competition(db, comp)

    assert comp.state == CompetitionState.active


# ── Mock price advancement ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_advances_mock_adapter_tick_on_each_cycle(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    adapter = MockDataAdapter.for_competition(comp.id, TICKERS)
    assert adapter.tick_num == 0

    await _snapshot_competition(db, comp)
    assert adapter.tick_num == 1

    await _snapshot_competition(db, comp)
    assert adapter.tick_num == 2


@pytest.mark.asyncio
async def test_snapshot_prices_change_after_each_tick(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await start_competition(db, comp.lobby_code, creator)
    await db.flush()

    await _snapshot_competition(db, comp)
    await db.flush()

    await _snapshot_competition(db, comp)
    await db.flush()

    all_snaps = await db.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.competition_id == comp.id, PriceSnapshot.ticker == "AAPL")
        .order_by(PriceSnapshot.recorded_at.asc())
    )
    snaps = all_snaps.scalars().all()
    assert snaps[0].price != snaps[1].price, "Price should change between ticks"


# ── price_service: snapshot-backed price queries ───────────────────────────────


@pytest.mark.asyncio
async def test_get_price_at_returns_correct_snapshot(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await db.flush()

    recorded = datetime.utcnow()
    expected = Decimal("142.50")
    db.add(PriceSnapshot(
        competition_id=comp.id,
        ticker="AAPL",
        price=expected,
        recorded_at=recorded,
        source=PriceSource.mock,
    ))
    await db.flush()

    price = await get_price_at(db, comp.id, "AAPL", recorded + timedelta(seconds=1))
    assert price == expected


@pytest.mark.asyncio
async def test_get_price_at_returns_none_when_no_snapshot_exists(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await db.flush()

    price = await get_price_at(db, comp.id, "AAPL", datetime.utcnow())
    assert price is None


@pytest.mark.asyncio
async def test_get_price_at_ignores_snapshots_after_requested_time(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await db.flush()

    future = datetime.utcnow() + timedelta(hours=1)
    db.add(PriceSnapshot(
        competition_id=comp.id,
        ticker="AAPL",
        price=Decimal("999.99"),
        recorded_at=future,
        source=PriceSource.mock,
    ))
    await db.flush()

    price = await get_price_at(db, comp.id, "AAPL", datetime.utcnow())
    assert price is None


@pytest.mark.asyncio
async def test_get_price_at_returns_latest_snapshot_before_cutoff(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await db.flush()

    now = datetime.utcnow()
    entries = [
        (now - timedelta(minutes=10), Decimal("100.00")),
        (now - timedelta(minutes=5), Decimal("110.00")),
        (now + timedelta(minutes=5), Decimal("120.00")),
    ]
    for ts, price in entries:
        db.add(PriceSnapshot(
            competition_id=comp.id, ticker="AAPL",
            price=price, recorded_at=ts, source=PriceSource.mock,
        ))
    await db.flush()

    price = await get_price_at(db, comp.id, "AAPL", now)
    assert price == Decimal("110.00")


@pytest.mark.asyncio
async def test_get_latest_snapshot_price_returns_most_recent(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await db.flush()

    now = datetime.utcnow()
    for i, p in enumerate(["50.00", "75.00", "99.00"]):
        db.add(PriceSnapshot(
            competition_id=comp.id, ticker="AAPL",
            price=Decimal(p), recorded_at=now + timedelta(minutes=i),
            source=PriceSource.mock,
        ))
    await db.flush()

    price = await get_latest_snapshot_price(db, comp.id, "AAPL")
    assert price == Decimal("99.00")


@pytest.mark.asyncio
async def test_get_latest_snapshot_price_returns_none_when_empty(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await db.flush()

    price = await get_latest_snapshot_price(db, comp.id, "AAPL")
    assert price is None


@pytest.mark.asyncio
async def test_get_price_timeline_returns_entries_in_chronological_order(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await db.flush()

    now = datetime.utcnow()
    ordered = [
        (now - timedelta(minutes=2), Decimal("50.00")),
        (now - timedelta(minutes=1), Decimal("60.00")),
        (now, Decimal("70.00")),
    ]
    for ts, price in ordered:
        db.add(PriceSnapshot(
            competition_id=comp.id, ticker="AAPL",
            price=price, recorded_at=ts, source=PriceSource.mock,
        ))
    await db.flush()

    timeline = await get_price_timeline(db, comp.id, "AAPL")

    assert len(timeline) == 3
    assert [p for _, p in timeline] == [Decimal("50.00"), Decimal("60.00"), Decimal("70.00")]
    timestamps = [t for t, _ in timeline]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_get_price_timeline_empty_when_no_snapshots(db):
    comp, creator, _ = await create_competition(db, _competition_data())
    await db.flush()

    timeline = await get_price_timeline(db, comp.id, "AAPL")
    assert timeline == []


# ── GET /players/{id}/history endpoint ────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_endpoint_returns_portfolio_snapshots(client, db):
    create_resp = await client.post(
        "/competitions",
        json={
            "name": "History Test",
            "creator_name": "Alice",
            "starting_balance": "10000",
            "asset_universe": ["AAPL", "TSLA"],
            "data_source": "mock",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    token, player_id, code = body["token"], body["player_id"], body["lobby_code"]

    start_resp = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert start_resp.status_code == 200

    from models.competition import Competition as Comp
    result = await db.execute(select(Comp).where(Comp.lobby_code == code))
    comp = result.scalar_one()
    await _snapshot_competition(db, comp)
    await db.flush()

    history_resp = await client.get(
        f"/players/{player_id}/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert history_resp.status_code == 200
    data = history_resp.json()
    assert data["player_id"] == player_id
    assert len(data["snapshots"]) >= 1

    snap = data["snapshots"][0]
    for field in ("recorded_at", "total_value", "cash_balance", "positions_value", "pnl"):
        assert field in snap, f"Missing field '{field}' in snapshot response"


@pytest.mark.asyncio
async def test_history_endpoint_returns_snapshots_in_chronological_order(client, db):
    create_resp = await client.post(
        "/competitions",
        json={
            "name": "History Order Test",
            "creator_name": "Alice",
            "starting_balance": "10000",
            "asset_universe": ["AAPL"],
            "data_source": "mock",
        },
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    token, player_id, code = body["token"], body["player_id"], body["lobby_code"]

    await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {token}"},
    )

    from models.competition import Competition as Comp
    result = await db.execute(select(Comp).where(Comp.lobby_code == code))
    comp = result.scalar_one()

    await _snapshot_competition(db, comp)
    await _snapshot_competition(db, comp)
    await db.flush()

    history_resp = await client.get(
        f"/players/{player_id}/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert history_resp.status_code == 200
    snapshots = history_resp.json()["snapshots"]
    assert len(snapshots) >= 2
    timestamps = [s["recorded_at"] for s in snapshots]
    assert timestamps == sorted(timestamps), "Snapshots not in chronological order"


@pytest.mark.asyncio
async def test_history_endpoint_requires_authentication(client):
    resp = await client.get("/players/some-id/history")
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_history_endpoint_rejects_cross_player_access(client, db):
    create_resp = await client.post(
        "/competitions",
        json={
            "name": "History Auth Test",
            "creator_name": "Alice",
            "starting_balance": "10000",
            "asset_universe": ["AAPL"],
            "data_source": "mock",
        },
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    alice_token, code = body["token"], body["lobby_code"]

    bob_resp = await client.post(
        f"/competitions/{code}/join",
        json={"display_name": "Bob"},
    )
    assert bob_resp.status_code == 201
    bob_id = bob_resp.json()["player_id"]

    resp = await client.get(
        f"/players/{bob_id}/history",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert resp.status_code == 403
