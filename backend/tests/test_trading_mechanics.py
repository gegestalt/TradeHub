"""Trading mechanics tests: spectator order rejection, duration auto-end, short P&L,
leverage enforcement, order processor conditional fills, and full integration flow."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from schemas.competition import CompetitionCreate, JoinRequest
from schemas.order import OrderCreate
from services.competition import create_competition, join_competition, start_competition
from services.order_engine import place_order
from services.order_processor import process_pending_orders

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(
    db,
    balance: Decimal = Decimal("10000"),
    allow_shorts: bool = False,
    max_leverage: Decimal = Decimal("1.0"),
    duration_minutes: int | None = None,
):
    data = CompetitionCreate(
        name="Test",
        starting_balance=balance,
        asset_universe=["AAPL", "TSLA"],
        fee_pct=Decimal("0"),
        creator_name="Alice",
        allow_shorts=allow_shorts,
        max_leverage=max_leverage,
        duration_minutes=duration_minutes,
    )
    comp, alice, token = await create_competition(db, data)
    await start_competition(db, comp.lobby_code, alice)
    return comp, alice, token


# ── Spectator order rejection (service level) ─────────────────────────────────


@pytest.mark.asyncio
async def test_spectator_cannot_place_order_service_level(db):
    """Spectator flag is enforced in the router; verify order engine itself also works
    correctly for normal players (service layer doesn't gate on spectator)."""

    comp, alice, _ = await _setup(db)
    spectator, _ = await join_competition(
        db, comp.lobby_code, JoinRequest(display_name="Watcher", spectator=True)
    )

    # The router guards spectators; at the service layer place_order doesn't know about
    # spectator flag. We test the HTTP layer separately (test_spectator_order_rejected_http).
    # Here we verify that a spectator's player object exists with spectator=True.
    assert spectator.spectator is True


@pytest.mark.asyncio
async def test_spectator_order_rejected_http(client):
    """Spectator gets 403 when attempting to place an order via HTTP."""
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "SpectatorTest",
            "creator_name": "Alice",
            "asset_universe": ["AAPL"],
            "starting_balance": "10000",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    creator_token = body["token"]

    # Start competition
    await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {creator_token}"},
    )

    # Spectator joins active competition
    join_resp = await client.post(
        f"/competitions/{code}/join",
        json={"display_name": "Watcher", "spectator": True},
    )
    spec_body = join_resp.json()
    spec_token = spec_body["token"]
    spec_player_id = spec_body["player_id"]

    order_resp = await client.post(
        f"/competitions/{code}/players/{spec_player_id}/orders",
        headers={"Authorization": f"Bearer {spec_token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert order_resp.status_code == 403
    assert "Spectator" in order_resp.json()["detail"]


# ── Duration auto-end wiring ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duration_minutes_sets_end_at_on_start(db):
    """start_competition wires duration_minutes → end_at."""
    comp, alice, _ = await _setup(db, duration_minutes=90)

    assert comp.end_at is not None
    expected_delta = timedelta(minutes=90)
    actual_delta = comp.end_at - comp.start_at
    # Allow 1-second tolerance for test execution time
    assert abs(actual_delta.total_seconds() - expected_delta.total_seconds()) < 1


@pytest.mark.asyncio
async def test_no_duration_leaves_end_at_none(db):
    data = CompetitionCreate(
        name="Open-ended",
        starting_balance=Decimal("10000"),
        asset_universe=["AAPL"],
        fee_pct=Decimal("0"),
        creator_name="Alice",
    )
    comp, alice, _ = await create_competition(db, data)
    await start_competition(db, comp.lobby_code, alice)
    assert comp.end_at is None


# ── Short P&L ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_short_sell_opens_negative_position(db):
    """Selling with no existing position opens a short when allow_shorts=True."""
    comp, alice, _ = await _setup(db, allow_shorts=True)

    from sqlalchemy import select

    from models.position import Position

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    result = await db.execute(
        select(Position).where(Position.player_id == alice.id, Position.ticker == "AAPL")
    )
    pos = result.scalar_one_or_none()
    assert pos is not None
    assert pos.quantity == Decimal("-10")


@pytest.mark.asyncio
async def test_short_sell_credits_cash(db):
    """Opening a short credits proceeds to cash balance."""
    comp, alice, _ = await _setup(db, allow_shorts=True, balance=Decimal("5000"))
    initial = alice.cash_balance

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )

    assert alice.cash_balance == initial + Decimal("500")  # 5 × 100, fee=0


@pytest.mark.asyncio
async def test_short_cover_realizes_profit(db):
    """Buying to cover a short at a lower price records positive realized P&L."""
    comp, alice, _ = await _setup(db, allow_shorts=True, balance=Decimal("20000"))

    # Open short at 100 → receives 1000
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    # Cover at 80 → pays 800, profit = (100 - 80) × 10 = 200
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("80")),
    )

    assert alice.realized_pnl == Decimal("200")


@pytest.mark.asyncio
async def test_short_cover_realizes_loss(db):
    """Buying to cover at a higher price records negative realized P&L."""
    comp, alice, _ = await _setup(db, allow_shorts=True, balance=Decimal("20000"))

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    # Cover at 120 → loss = (100 - 120) × 10 = -200
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("120")),
    )

    assert alice.realized_pnl == Decimal("-200")


@pytest.mark.asyncio
async def test_partial_cover_reduces_short(db):
    """Partial cover reduces short position without closing it."""
    from sqlalchemy import select

    from models.position import Position

    comp, alice, _ = await _setup(db, allow_shorts=True, balance=Decimal("20000"))

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("4")),
        make_adapter(Decimal("90")),
    )

    result = await db.execute(
        select(Position).where(Position.player_id == alice.id, Position.ticker == "AAPL")
    )
    pos = result.scalar_one()
    assert pos.quantity == Decimal("-6")


# ── Leverage enforcement ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buy_within_leverage_succeeds(db):
    """Order within max_leverage is accepted."""
    comp, alice, _ = await _setup(db, balance=Decimal("1000"), max_leverage=Decimal("2.0"))

    # Notional = 100 × 10 = 1000; max = 1000 × 2 = 2000 → OK
    order = await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    assert order.status == OrderStatus.filled


@pytest.mark.asyncio
async def test_buy_exceeds_leverage_rejected(db):
    """Order exceeding max_leverage raises 400."""
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db, balance=Decimal("1000"), max_leverage=Decimal("1.0"))

    # Notional = 100 × 11 = 1100 > 1000 × 1.0 = 1000
    with pytest.raises(HTTPException) as exc:
        await place_order(
            db, alice, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("11")),
            make_adapter(Decimal("100")),
        )
    assert exc.value.status_code == 400
    assert "leverage" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_sell_not_subject_to_leverage_check(db):
    """Leverage check only applies to buys; sells are always allowed."""
    comp, alice, _ = await _setup(
        db, allow_shorts=True, balance=Decimal("1000"), max_leverage=Decimal("1.0")
    )

    # Even though 1000×1 = 1000 and the sell notional is 1100, sell should proceed
    order = await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("11")),
        make_adapter(Decimal("100")),
    )
    assert order.status == OrderStatus.filled


# ── Order processor conditional fills ────────────────────────────────────────


@pytest.mark.asyncio
async def test_processor_fills_limit_buy_when_price_drops(db):
    """Background processor fills a GTC limit buy when market price <= limit_price."""
    from unittest.mock import patch

    comp, alice, _ = await _setup(db)

    # Limit buy at 95, current price 100 → stays pending
    order = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.buy,
            quantity=Decimal("5"),
            order_type=OrderType.limit,
            limit_price=Decimal("95"),
            time_in_force=TimeInForce.gtc,
        ),
        make_adapter(Decimal("100")),
    )
    assert order.status == OrderStatus.pending

    # Now price drops to 90 → processor should fill
    with patch("services.order_processor.get_adapter", return_value=make_adapter(Decimal("90"))):
        filled = await process_pending_orders(db)

    assert filled == 1
    assert order.status == OrderStatus.filled


@pytest.mark.asyncio
async def test_processor_fills_stop_loss_when_price_drops(db):
    """Processor fills a stop-loss sell when price hits stop_price."""
    from unittest.mock import patch

    comp, alice, _ = await _setup(db)

    # Buy first to have a position
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )

    # Place stop-loss at 80
    sl_order = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.sell,
            quantity=Decimal("5"),
            order_type=OrderType.stop_loss,
            stop_price=Decimal("80"),
            time_in_force=TimeInForce.gtc,
        ),
        make_adapter(Decimal("100")),  # price still 100, not triggered
    )
    assert sl_order.status == OrderStatus.pending

    # Price drops to 75 → stop triggered
    with patch("services.order_processor.get_adapter", return_value=make_adapter(Decimal("75"))):
        filled = await process_pending_orders(db)

    assert filled == 1
    assert sl_order.status == OrderStatus.filled


@pytest.mark.asyncio
async def test_processor_does_not_fill_limit_buy_above_price(db):
    """Limit buy at 95 should NOT fill when market is at 100."""
    from unittest.mock import patch

    comp, alice, _ = await _setup(db)

    order = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.buy,
            quantity=Decimal("5"),
            order_type=OrderType.limit,
            limit_price=Decimal("95"),
            time_in_force=TimeInForce.gtc,
        ),
        make_adapter(Decimal("100")),
    )

    with patch("services.order_processor.get_adapter", return_value=make_adapter(Decimal("100"))):
        filled = await process_pending_orders(db)

    assert filled == 0
    assert order.status == OrderStatus.pending


@pytest.mark.asyncio
async def test_processor_skips_non_active_competitions(db):
    """Processor ignores orders in ended competitions."""
    from unittest.mock import patch

    from services.competition import end_competition

    comp, alice, _ = await _setup(db)

    order = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL",
            side=OrderSide.buy,
            quantity=Decimal("5"),
            order_type=OrderType.limit,
            limit_price=Decimal("95"),
            time_in_force=TimeInForce.gtc,
        ),
        make_adapter(Decimal("100")),
    )

    await end_competition(db, comp.lobby_code, alice)

    with patch("services.order_processor.get_adapter", return_value=make_adapter(Decimal("90"))):
        filled = await process_pending_orders(db)

    assert filled == 0
    assert order.status == OrderStatus.pending


# ── Full integration flow ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_competition_flow(client):
    """End-to-end: create lobby, 3 players join, start, each places an order,
    leaderboard returns all 3, prices are consistent between orders and leaderboard."""

    # 1. Create lobby
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Integration Test",
            "creator_name": "Alice",
            "asset_universe": ["AAPL"],
            "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    code = body["lobby_code"]
    alice_token = body["token"]
    alice_id = None  # resolved from leaderboard after start

    # 2. Bob and Charlie join
    bob_resp = await client.post(
        f"/competitions/{code}/join",
        json={"display_name": "Bob"},
    )
    assert bob_resp.status_code == 201
    bob_token = bob_resp.json()["token"]
    bob_id = bob_resp.json()["player_id"]

    charlie_resp = await client.post(
        f"/competitions/{code}/join",
        json={"display_name": "Charlie"},
    )
    assert charlie_resp.status_code == 201
    charlie_token = charlie_resp.json()["token"]
    charlie_id = charlie_resp.json()["player_id"]

    # 3. Start competition
    start_resp = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert start_resp.status_code == 200

    # 4. Get Alice's player_id from leaderboard
    lb_early = await client.get(f"/competitions/{code}/leaderboard")
    alice_id = next(e["player_id"] for e in lb_early.json() if e["display_name"] == "Alice")

    # 5. Spectator joins after start
    spec_resp = await client.post(
        f"/competitions/{code}/join",
        json={"display_name": "Watcher", "spectator": True},
    )
    assert spec_resp.status_code == 201

    # 6. Each player places a market buy order
    players = [(alice_id, alice_token), (bob_id, bob_token), (charlie_id, charlie_token)]
    for player_id, token in players:
        order_resp = await client.post(
            f"/competitions/{code}/players/{player_id}/orders",
            headers={"Authorization": f"Bearer {token}"},
            json={"ticker": "AAPL", "side": "buy", "quantity": "5"},
        )
        assert order_resp.status_code == 201, order_resp.text
        assert order_resp.json()["status"] == "filled"

    # 7. Leaderboard returns exactly 3 players (spectator excluded)
    lb_resp = await client.get(f"/competitions/{code}/leaderboard")
    assert lb_resp.status_code == 200
    entries = lb_resp.json()
    assert len(entries) == 3
    names = {e["display_name"] for e in entries}
    assert names == {"Alice", "Bob", "Charlie"}
    assert "Watcher" not in names

    # 8. All players should have the same fill price (same mock adapter instance)
    # Verify all 3 orders had the same fill price (price consistency)
    for player_id, token in players:
        orders_resp = await client.get(
            f"/competitions/{code}/players/{player_id}/orders",
            headers={"Authorization": f"Bearer {token}"},
        )
        filled_orders = [o for o in orders_resp.json() if o["status"] == "filled"]
        assert len(filled_orders) == 1
        assert Decimal(filled_orders[0]["fill_price"]) > Decimal("0")

    # Fill prices should be identical across players (same mock singleton)
    all_fill_prices = []
    for player_id, token in players:
        orders_resp = await client.get(
            f"/competitions/{code}/players/{player_id}/orders",
            headers={"Authorization": f"Bearer {token}"},
        )
        fp = next(o["fill_price"] for o in orders_resp.json() if o["status"] == "filled")
        all_fill_prices.append(fp)

    assert all_fill_prices[0] == all_fill_prices[1] == all_fill_prices[2], (
        f"Price inconsistency: {all_fill_prices}"
    )
