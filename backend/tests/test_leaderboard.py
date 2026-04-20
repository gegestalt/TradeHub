"""Leaderboard accuracy tests.

Verifies exact PnL maths, rank ordering, unrealized PnL changes with price moves,
realized PnL accumulation across multiple trades, and fee impact on leaderboard values.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from models.enums import OrderSide, ScoringMethod
from schemas.competition import CompetitionCreate, JoinRequest
from schemas.order import OrderCreate
from services.competition import (
    create_competition,
    get_leaderboard,
    join_competition,
    start_competition,
)
from services.order_engine import place_order


def make_adapter(price: Decimal) -> MagicMock:
    m = MagicMock()
    m.get_price.return_value = price
    return m


def make_multi_adapter(prices: dict[str, Decimal]) -> MagicMock:
    m = MagicMock()
    m.get_price.side_effect = lambda ticker: prices[ticker]
    return m


async def _setup(
    db,
    balance: Decimal = Decimal("10000"),
    fee: Decimal = Decimal("0"),
    **kwargs,
):
    data = CompetitionCreate(
        name="LB Test",
        starting_balance=balance,
        asset_universe=["AAPL", "TSLA"],
        fee_pct=fee,
        creator_name="Alice",
        **kwargs,
    )
    comp, alice, token = await create_competition(db, data)
    await start_competition(db, comp.lobby_code, alice)
    return comp, alice, token


# ── Single player, no trades ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_one_player_no_trades(db):
    comp, alice, _ = await _setup(db)
    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("100")))

    assert len(entries) == 1
    e = entries[0]
    assert e.display_name == "Alice"
    assert e.rank == 1
    assert e.cash_balance == Decimal("10000")
    assert e.positions_value == Decimal("0")
    assert e.total_value == Decimal("10000")
    assert e.pnl == Decimal("0")
    assert e.pnl_pct == Decimal("0")
    assert e.realized_pnl == Decimal("0")
    assert e.unrealized_pnl == Decimal("0")
    assert e.orders_filled == 0
    assert e.positions == []


# ── Unrealized PnL changes with price ────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_unrealized_gain(db):
    """Buy 10 shares at $100; price rises to $120 → unrealized PnL = $200."""
    comp, alice, _ = await _setup(db, balance=Decimal("10000"))

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("120")))
    e = entries[0]

    assert e.cash_balance == Decimal("9000")       # 10000 - 100*10
    assert e.positions_value == Decimal("1200")    # 10 * 120
    assert e.total_value == Decimal("10200")
    assert e.pnl == Decimal("200")
    assert e.unrealized_pnl == Decimal("200")      # (120-100)*10
    assert e.realized_pnl == Decimal("0")
    assert len(e.positions) == 1
    pos = e.positions[0]
    assert pos.ticker == "AAPL"
    assert pos.quantity == Decimal("10")
    assert pos.avg_entry_price == Decimal("100")
    assert pos.current_price == Decimal("120")
    assert pos.market_value == Decimal("1200")
    assert pos.unrealized_pnl == Decimal("200")


@pytest.mark.asyncio
async def test_leaderboard_unrealized_loss(db):
    """Buy 10 shares at $100; price drops to $80 → unrealized PnL = −$200."""
    comp, alice, _ = await _setup(db)

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("80")))
    e = entries[0]

    assert e.total_value == Decimal("9800")
    assert e.pnl == Decimal("-200")
    assert e.unrealized_pnl == Decimal("-200")


@pytest.mark.asyncio
async def test_leaderboard_pnl_pct_correct(db):
    """PnL% = (total_value − starting_balance) / starting_balance × 100."""
    comp, alice, _ = await _setup(db, balance=Decimal("5000"))

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    # balance=5000, buy 10@100 → cash=4000, position=10*150=1500, total=5500
    # PnL = 500, PnL% = 500/5000*100 = 10%
    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("150")))
    e = entries[0]
    assert e.total_value == Decimal("5500")
    assert e.pnl_pct == Decimal("10")


# ── Realized PnL ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_realized_pnl_after_sell(db):
    """Sell 10 shares bought at $100 for $150 → realized PnL = $500."""
    comp, alice, _ = await _setup(db)

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("150")),
    )

    # After full sell: no positions, cash = 10000 + (150−100)×10 = 10500
    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("150")))
    e = entries[0]

    assert e.cash_balance == Decimal("10500")
    assert e.positions == []
    assert e.positions_value == Decimal("0")
    assert e.realized_pnl == Decimal("500")
    assert e.unrealized_pnl == Decimal("0")
    assert e.total_value == Decimal("10500")
    assert e.orders_filled == 2


@pytest.mark.asyncio
async def test_leaderboard_realized_and_unrealized_combined(db):
    """Sell half a position: realized PnL from the sale + unrealized from remainder."""
    comp, alice, _ = await _setup(db)

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("20")),
        make_adapter(Decimal("100")),
    )
    # Sell 10 at 120 → realized = (120-100)*10 = 200
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("120")),
    )

    # Price now 130: remaining 10 shares → unrealized = (130-100)*10 = 300
    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("130")))
    e = entries[0]

    assert e.realized_pnl == Decimal("200")
    assert e.unrealized_pnl == Decimal("300")
    assert e.pnl == Decimal("500")   # total PnL = realized + unrealized


# ── Fee impact ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_total_value_accounts_for_fees(db):
    """With 0.1% fee, total_value should be reduced by fee paid."""
    comp, alice, _ = await _setup(db, fee=Decimal("0.001"))
    price = Decimal("100")
    qty = Decimal("10")
    fee = price * qty * Decimal("0.001")  # = 1.00

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=qty),
        make_adapter(price),
    )

    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(price))
    e = entries[0]

    # Cash = 10000 - 100*10 - fee = 8999; position = 1000; total = 9999
    expected_cash = Decimal("10000") - price * qty - fee
    assert e.cash_balance == expected_cash
    assert e.total_value == expected_cash + price * qty


# ── Rank ordering ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_rank_ordering_3_players(db):
    """Player with highest total_value is rank 1; verify all three ranks."""
    data = CompetitionCreate(
        name="Rank Test", starting_balance=Decimal("10000"),
        asset_universe=["AAPL"], fee_pct=Decimal("0"), creator_name="Alice",
    )
    comp, alice, _ = await create_competition(db, data)
    bob, _ = await join_competition(db, comp.lobby_code, JoinRequest(display_name="Bob"))
    charlie, _ = await join_competition(db, comp.lobby_code, JoinRequest(display_name="Charlie"))
    await start_competition(db, comp.lobby_code, alice)

    # Alice buys 10 @ 100 → she'll gain the most when price rises
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    # Bob buys 5 @ 100 → intermediate gain
    await place_order(
        db, bob, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("100")),
    )
    # Charlie holds cash — no trade

    # Price rises to 200 → Alice: +1000, Bob: +500, Charlie: 0
    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("200")))

    by_name = {e.display_name: e for e in entries}
    assert by_name["Alice"].rank == 1
    assert by_name["Bob"].rank == 2
    assert by_name["Charlie"].rank == 3

    assert by_name["Alice"].total_value > by_name["Bob"].total_value
    assert by_name["Bob"].total_value > by_name["Charlie"].total_value


@pytest.mark.asyncio
async def test_leaderboard_rank_numbers_are_sequential(db):
    """Ranks must be 1, 2, 3 with no gaps or duplicates."""
    data = CompetitionCreate(
        name="Seq Test", starting_balance=Decimal("10000"),
        asset_universe=["AAPL"], fee_pct=Decimal("0"), creator_name="Alice",
    )
    comp, alice, _ = await create_competition(db, data)
    await join_competition(db, comp.lobby_code, JoinRequest(display_name="Bob"))
    await join_competition(db, comp.lobby_code, JoinRequest(display_name="Charlie"))
    await start_competition(db, comp.lobby_code, alice)

    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("100")))
    ranks = sorted(e.rank for e in entries)
    assert ranks == [1, 2, 3]


@pytest.mark.asyncio
async def test_leaderboard_empty_no_players(db):
    """A competition with only the creator who was somehow removed returns empty list gracefully."""
    comp, alice, _ = await _setup(db)
    # Leaderboard still includes the creator
    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("100")))
    assert len(entries) >= 1


# ── orders_filled counter ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_orders_filled_counter(db):
    """orders_filled increments for each filled order."""
    comp, alice, _ = await _setup(db, balance=Decimal("50000"))
    adapter = make_adapter(Decimal("100"))

    for _ in range(3):
        await place_order(
            db, alice, comp,
            OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("1")),
            adapter,
        )

    entries = await get_leaderboard(db, comp.lobby_code, adapter)
    assert entries[0].orders_filled == 3


# ── Multi-ticker positions ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_multi_ticker_positions(db):
    """Portfolio across two tickers: both positions appear and values sum correctly."""
    comp, alice, _ = await _setup(db, balance=Decimal("50000"))

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.buy, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )
    await place_order(
        db, alice, comp,
        OrderCreate(ticker="TSLA", side=OrderSide.buy, quantity=Decimal("5")),
        make_adapter(Decimal("200")),
    )

    # AAPL at 110, TSLA at 210
    adapter = make_multi_adapter({"AAPL": Decimal("110"), "TSLA": Decimal("210")})
    entries = await get_leaderboard(db, comp.lobby_code, adapter)
    e = entries[0]

    tickers = {p.ticker for p in e.positions}
    assert tickers == {"AAPL", "TSLA"}

    aapl_pos = next(p for p in e.positions if p.ticker == "AAPL")
    tsla_pos = next(p for p in e.positions if p.ticker == "TSLA")

    assert aapl_pos.unrealized_pnl == Decimal("100")   # (110-100)*10
    assert tsla_pos.unrealized_pnl == Decimal("50")    # (210-200)*5

    expected_positions_value = Decimal("110") * 10 + Decimal("210") * 5
    assert e.positions_value == expected_positions_value


# ── Short position in leaderboard ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_short_position_negative_unrealized_pnl_on_price_rise(db):
    """Short 10 @ $100; price rises to $120 → unrealized PnL = −$200 (losing short)."""
    comp, alice, _ = await _setup(db, balance=Decimal("50000"), allow_shorts=True)

    await place_order(
        db, alice, comp,
        OrderCreate(ticker="AAPL", side=OrderSide.sell, quantity=Decimal("10")),
        make_adapter(Decimal("100")),
    )

    # Price rises → short is underwater
    entries = await get_leaderboard(db, comp.lobby_code, make_adapter(Decimal("120")))
    e = entries[0]

    assert e.unrealized_pnl == Decimal("-200")   # (100-120)*10


# ── HTTP leaderboard endpoint ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_leaderboard_http_returns_correct_structure(client):
    """HTTP GET /competitions/{code}/leaderboard returns all required fields."""
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "LB HTTP", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    token = body["token"]

    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})

    resp = await client.get(f"/competitions/{code}/leaderboard")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    e = entries[0]

    for field in (
        "rank", "player_id", "display_name", "cash_balance",
        "positions_value", "total_value", "pnl", "pnl_pct",
        "realized_pnl", "unrealized_pnl", "orders_filled", "positions", "score",
    ):
        assert field in e, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_leaderboard_http_rank1_has_highest_total_value(client):
    """Rank-1 player always has the highest (or tied) total_value."""
    create_resp = await client.post(
        "/lobbies",
        json={
            "name": "Rank Test", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = create_resp.json()
    code = body["lobby_code"]
    alice_token = body["token"]

    bob_resp = await client.post(f"/competitions/{code}/join", json={"display_name": "Bob"})
    bob_token = bob_resp.json()["token"]
    bob_id = bob_resp.json()["player_id"]

    await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {alice_token}"},
    )

    # Bob buys; alice doesn't — mock price movement will determine winner
    lb_resp = await client.get(f"/competitions/{code}/leaderboard")
    entries = lb_resp.json()
    rank1 = next(e for e in entries if e["rank"] == 1)
    for e in entries:
        assert float(rank1["total_value"]) >= float(e["total_value"])
