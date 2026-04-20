"""Order management tests: cancellation, list filtering, competition state guards,
partial sells, fee effects on realized P&L, order validation edge cases."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from schemas.competition import CompetitionCreate, JoinRequest
from schemas.order import OrderCreate
from services.competition import (
    create_competition,
    end_competition,
    join_competition,
    start_competition,
)
from services.order_engine import cancel_order, place_order


def make_adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


async def _setup(db, balance: Decimal = Decimal("10000"), fee: Decimal = Decimal("0"), **kwargs):
    data = CompetitionCreate(
        name="OM Test",
        starting_balance=balance,
        asset_universe=["AAPL", "TSLA"],
        fee_pct=fee,
        creator_name="Alice",
        **kwargs,
    )
    comp, alice, token = await create_competition(db, data)
    await start_competition(db, comp.lobby_code, alice)
    return comp, alice, token


# ── Order cancellation (service level) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_pending_order_sets_cancelled_status(db):
    comp, alice, _ = await _setup(db)

    order = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy,
            order_type=OrderType.limit, quantity=Decimal("5"), limit_price=Decimal("80"),
        ),
        make_adapter(Decimal("100")),  # price above limit → stays pending
    )
    assert order.status == OrderStatus.pending

    cancelled = await cancel_order(db, alice, order.id)
    assert cancelled.status == OrderStatus.cancelled


@pytest.mark.asyncio
async def test_cancel_pending_order_does_not_deduct_balance(db):
    """Cancelling a pending limit order must not charge the player."""
    comp, alice, _ = await _setup(db, balance=Decimal("1000"))
    initial = alice.cash_balance

    order = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy,
            order_type=OrderType.limit, quantity=Decimal("5"), limit_price=Decimal("80"),
        ),
        make_adapter(Decimal("100")),
    )
    await cancel_order(db, alice, order.id)
    assert alice.cash_balance == initial


@pytest.mark.asyncio
async def test_cancel_filled_order_raises_400(db):
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)
    order = await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
        make_adapter(Decimal("100")),
    )
    assert order.status == OrderStatus.filled

    with pytest.raises(HTTPException) as exc:
        await cancel_order(db, alice, order.id)
    assert exc.value.status_code == 400
    assert "filled" in exc.value.detail or "Cannot cancel" in exc.value.detail


@pytest.mark.asyncio
async def test_cancel_already_cancelled_order_raises_400(db):
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)
    order = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy,
            order_type=OrderType.limit, quantity=Decimal("5"), limit_price=Decimal("80"),
        ),
        make_adapter(Decimal("100")),
    )
    await cancel_order(db, alice, order.id)

    with pytest.raises(HTTPException) as exc:
        await cancel_order(db, alice, order.id)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cancel_nonexistent_order_raises_404(db):
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await cancel_order(db, alice, "00000000-0000-0000-0000-000000000000")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_other_players_order_raises_404(db):
    """Player B cannot cancel player A's order — should 404 (not visible)."""
    from fastapi import HTTPException

    data = CompetitionCreate(
        name="Cancel Guard", starting_balance=Decimal("10000"),
        asset_universe=["AAPL", "TSLA"], fee_pct=Decimal("0"), creator_name="Alice",
    )
    comp, alice, _ = await create_competition(db, data)
    bob, _ = await join_competition(db, comp.lobby_code, JoinRequest(display_name="Bob"))
    await start_competition(db, comp.lobby_code, alice)

    order = await place_order(
        db, alice, comp,
        OrderCreate(
            ticker="AAPL", side=OrderSide.buy,
            order_type=OrderType.limit, quantity=Decimal("5"), limit_price=Decimal("80"),
        ),
        make_adapter(Decimal("100")),
    )

    with pytest.raises(HTTPException) as exc:
        await cancel_order(db, bob, order.id)  # bob tries to cancel alice's order
    assert exc.value.status_code == 404


# ── Order cancellation (HTTP level) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_order_http_returns_cancelled_status(client):
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Cancel Test", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]
    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})
    lb = await client.get(f"/competitions/{code}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    # Place a limit buy that won't fill immediately (limit far below mock price)
    order_resp = await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "5",
              "order_type": "limit", "limit_price": "1"},
    )
    assert order_resp.status_code == 201
    order_id = order_resp.json()["id"]
    assert order_resp.json()["status"] == "pending"

    cancel_resp = await client.delete(
        f"/competitions/{code}/players/{player_id}/orders/{order_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"


# ── Competition state guards (HTTP) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_in_lobby_state_returns_400(client):
    """Cannot place orders while competition is still in lobby state."""
    create_resp = await client.post(
        "/competitions",
        json={
            "name": "Lobby Guard", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]
    player_id = body["player_id"]

    resp = await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert resp.status_code == 400
    assert "not active" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_order_in_ended_competition_returns_400(client):
    """Cannot place orders after competition has ended."""
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Ended Guard", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]

    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})
    await client.post(f"/competitions/{code}/end", headers={"Authorization": f"Bearer {token}"})

    lb = await client.get(f"/competitions/{code}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    resp = await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert resp.status_code == 400


# ── Order list with status filter ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_orders_filter_filled(client):
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "List Filter", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "50000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]
    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})
    lb = await client.get(f"/competitions/{code}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    # Place a market buy (fills immediately) and a limit buy (stays pending)
    await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1",
              "order_type": "limit", "limit_price": "1"},
    )

    # Filter by filled
    filled_resp = await client.get(
        f"/competitions/{code}/players/{player_id}/orders?status=filled",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert filled_resp.status_code == 200
    filled = filled_resp.json()
    assert all(o["status"] == "filled" for o in filled)
    assert len(filled) == 1

    # Filter by pending
    pending_resp = await client.get(
        f"/competitions/{code}/players/{player_id}/orders?status=pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    pending = pending_resp.json()
    assert all(o["status"] == "pending" for o in pending)
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_list_orders_no_filter_returns_all(client):
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "List All", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "50000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]
    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})
    lb = await client.get(f"/competitions/{code}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1",
              "order_type": "limit", "limit_price": "1"},
    )

    all_resp = await client.get(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert all_resp.status_code == 200
    assert len(all_resp.json()) == 2


# ── Partial sell ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_sell_reduces_position(db):
    """Buy 10 shares, sell 4 → position = 6."""
    from sqlalchemy import select

    from models.position import Position

    comp, alice, _ = await _setup(db)

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("4")),
        make_adapter(Decimal("110")),
    )

    result = await db.execute(
        select(Position).where(Position.player_id == alice.id, Position.ticker == "AAPL")
    )
    pos = result.scalar_one()
    assert pos.quantity == Decimal("6")


@pytest.mark.asyncio
async def test_partial_sell_realizes_correct_pnl(db):
    """Partial sell realizes PnL only on the sold portion."""
    comp, alice, _ = await _setup(db)

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    # Sell 4 at 120 → realized = (120-100)*4 = 80
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("4")),
        make_adapter(Decimal("120")),
    )

    assert alice.realized_pnl == Decimal("80")


@pytest.mark.asyncio
async def test_sell_more_than_position_fails(db):
    """Selling more shares than owned raises 400."""
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )

    with pytest.raises(HTTPException) as exc:
        await place_order(
            db, alice, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("6")),
            make_adapter(Decimal("100")),
        )
    assert exc.value.status_code == 400


# ── Fee effects on realized P&L ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fee_reduces_realized_pnl_on_sell(db):
    """With 0.1% fee: buy 10@100, sell 10@150. Realized = (50*10) - buy_fee - sell_fee."""
    fee_pct = Decimal("0.001")
    comp, alice, _ = await _setup(db, fee=fee_pct)

    buy_price, sell_price, qty = Decimal("100"), Decimal("150"), Decimal("10")
    buy_fee = buy_price * qty * fee_pct
    sell_fee = sell_price * qty * fee_pct

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=qty),
        make_adapter(buy_price),
    )
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=qty),
        make_adapter(sell_price),
    )

    gross_pnl = (sell_price - buy_price) * qty  # 500
    # Sell fee is deducted from realized_pnl (buy fee reduces cash but not pnl directly)
    expected_realized = gross_pnl - sell_fee
    assert alice.realized_pnl == expected_realized.quantize(Decimal("0.00000001"))


@pytest.mark.asyncio
async def test_fee_accumulates_across_multiple_trades(db):
    """Fee paid across 3 buy orders reduces cash proportionally."""
    fee_pct = Decimal("0.001")
    comp, alice, _ = await _setup(db, balance=Decimal("50000"), fee=fee_pct)
    adapter = make_adapter(Decimal("100"))
    qty = Decimal("10")
    price = Decimal("100")
    fee_per_trade = price * qty * fee_pct

    for _ in range(3):
        await place_order(
            db, alice, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=qty),
            adapter,
        )

    expected_cash = Decimal("50000") - 3 * (price * qty + fee_per_trade)
    assert alice.cash_balance == expected_cash.quantize(Decimal("0.00000001"))


# ── Order validation edge cases ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_invalid_ticker_not_in_universe_rejected(db):
    """Ticker outside the competition's asset_universe is rejected."""
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)

    with pytest.raises(HTTPException) as exc:
        await place_order(
            db, alice, comp,
            OrderCreate(ticker="AMZN", side=OrderSide.buy, quantity=Decimal("1")),
            make_adapter(Decimal("100")),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_order_ticker_case_insensitive_rejection(db):
    """Lower-case ticker not in universe should also be rejected."""
    from fastapi import HTTPException

    comp, alice, _ = await _setup(db)

    with pytest.raises(HTTPException) as exc:
        await place_order(
            db, alice, comp,
            OrderCreate(ticker="amzn", side=OrderSide.buy, quantity=Decimal("1")),
            make_adapter(Decimal("100")),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_order_zero_quantity_rejected_by_schema():
    """Schema validation: quantity must be > 0."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("0"))


@pytest.mark.asyncio
async def test_order_negative_quantity_rejected_by_schema():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("-5"))


# ── Competition trades endpoint ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_competition_trades_returns_filled_orders(client):
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Trades Feed", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "50000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]
    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})
    lb = await client.get(f"/competitions/{code}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "5"},
    )

    trades_resp = await client.get(f"/competitions/{code}/trades")
    assert trades_resp.status_code == 200
    trades = trades_resp.json()
    assert len(trades) >= 1


@pytest.mark.asyncio
async def test_competition_trades_empty_before_any_orders(client):
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Empty Trades", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]
    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})

    trades_resp = await client.get(f"/competitions/{code}/trades")
    assert trades_resp.status_code == 200
    assert trades_resp.json() == []


@pytest.mark.asyncio
async def test_competition_trades_limit_param(client):
    """limit query param caps the number of returned trades."""
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Trades Limit", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "500000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]
    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})
    lb = await client.get(f"/competitions/{code}/leaderboard")
    player_id = lb.json()[0]["player_id"]

    for _ in range(5):
        await client.post(
            f"/competitions/{code}/players/{player_id}/orders",
            headers={"Authorization": f"Bearer {token}"},
            json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
        )

    trades_resp = await client.get(f"/competitions/{code}/trades?limit=3")
    assert trades_resp.status_code == 200
    assert len(trades_resp.json()) <= 3
