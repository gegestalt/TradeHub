"""Tests for market-closed behaviour.

Three layers are verified:

  1. Schema — /prices/{ticker}/status always returns a well-formed response
     with the right keys, whether the market is open or closed.

  2. Adapter fallback — OnlineDataAdapter.get_price returns yfinance's
     previous_close when last_price is None (i.e. outside trading hours).

  3. Order fills — orders placed when the market is closed still fill, and
     the fill_price equals whatever the adapter returned (the last close).
     The fill logic is market-hours-agnostic: it trusts the adapter.

All tests that touch the network are mocked so the suite passes at any hour.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import create_lobby_http


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _status_dict(is_open: bool) -> dict:
    if is_open:
        return {
            "market_open": True,
            "market_status": "Market is open (NYSE/NASDAQ regular hours)",
            "next_open_at": None,
            "timezone": "America/New_York",
        }
    return {
        "market_open": False,
        "market_status": "Market closed — after-hours",
        "next_open_at": "2026-04-28T09:30:00-04:00",
        "timezone": "America/New_York",
    }


def _mock_market_status(is_open: bool) -> MagicMock:
    d = _status_dict(is_open)
    ms = MagicMock()
    ms.is_open = is_open
    ms.to_dict.return_value = d
    return ms


def _mock_yf_ticker(last_price=None, previous_close: float = 185.50) -> MagicMock:
    """Simulate yf.Ticker(ticker).fast_info for open or closed market."""
    fast_info = MagicMock()
    fast_info.last_price = last_price
    fast_info.previous_close = previous_close
    ticker = MagicMock()
    ticker.fast_info = fast_info
    return ticker


# ── 1. /prices/{ticker}/status endpoint schema ─────────────────────────────────

@pytest.mark.asyncio
async def test_status_endpoint_schema_when_market_open(client):
    """Response has all required keys and next_open_at is None when open."""
    with patch("routers.prices.get_market_status", return_value=_mock_market_status(True)):
        r = await client.get("/prices/AAPL/status")

    assert r.status_code == 200
    data = r.json()
    assert data["market_open"] is True
    assert data["next_open_at"] is None
    assert "market_status" in data
    assert "timezone" in data


@pytest.mark.asyncio
async def test_status_endpoint_schema_when_market_closed(client):
    """Response has all required keys and next_open_at is populated when closed."""
    with patch("routers.prices.get_market_status", return_value=_mock_market_status(False)):
        r = await client.get("/prices/AAPL/status")

    assert r.status_code == 200
    data = r.json()
    assert data["market_open"] is False
    assert data["next_open_at"] is not None
    assert "market_status" in data
    assert "timezone" in data


@pytest.mark.asyncio
async def test_status_endpoint_crypto_always_open(client):
    """Crypto tickers return market_open=True regardless of time."""
    crypto_status = MagicMock()
    crypto_status.to_dict.return_value = {
        "market_open": True,
        "market_status": "Crypto trades 24/7",
        "next_open_at": None,
        "timezone": "UTC",
    }
    with patch("routers.prices.get_market_status", return_value=crypto_status):
        r = await client.get("/prices/BTC-USD/status")

    assert r.status_code == 200
    assert r.json()["market_open"] is True


# ── 2. Adapter price fallback ──────────────────────────────────────────────────

def test_adapter_uses_last_price_when_market_open():
    """OnlineDataAdapter.get_price returns fast_info.last_price during market hours."""
    live_price = 192.30
    with patch("yfinance.Ticker", return_value=_mock_yf_ticker(last_price=live_price,
                                                                previous_close=185.50)):
        from data_adapters.online import OnlineDataAdapter
        price = OnlineDataAdapter().get_price("AAPL")

    assert price == Decimal(str(live_price))


def test_adapter_falls_back_to_previous_close_when_market_closed():
    """When last_price is None (market closed), get_price falls back to previous_close."""
    closing_price = 185.50
    with patch("yfinance.Ticker", return_value=_mock_yf_ticker(last_price=None,
                                                                previous_close=closing_price)):
        from data_adapters.online import OnlineDataAdapter
        price = OnlineDataAdapter().get_price("AAPL")

    assert price == Decimal(str(closing_price))


def test_adapter_raises_when_both_prices_unavailable():
    """get_price raises ValueError if yfinance returns no usable price at all."""
    with patch("yfinance.Ticker", return_value=_mock_yf_ticker(last_price=None,
                                                                previous_close=None)):
        from data_adapters.online import OnlineDataAdapter
        with pytest.raises(ValueError, match="Could not fetch live price"):
            OnlineDataAdapter().get_price("AAPL")


# ── 3. /prices/{ticker} endpoint — price + status together ────────────────────

@pytest.mark.asyncio
async def test_price_endpoint_returns_live_price_when_market_open(client):
    """GET /prices/{ticker} returns the live last_price when the market is open."""
    live_price = Decimal("192.30")
    with (
        patch("routers.prices.get_market_status", return_value=_mock_market_status(True)),
        patch("data_adapters.online.OnlineDataAdapter.get_price", return_value=live_price),
    ):
        r = await client.get("/prices/AAPL")

    assert r.status_code == 200
    data = r.json()
    assert data["market_open"] is True
    assert Decimal(data["price"]) == live_price


@pytest.mark.asyncio
async def test_price_endpoint_returns_closing_price_when_market_closed(client):
    """GET /prices/{ticker} returns previous_close when the exchange is closed."""
    closing_price = Decimal("185.50")
    with (
        patch("routers.prices.get_market_status", return_value=_mock_market_status(False)),
        patch("data_adapters.online.OnlineDataAdapter.get_price", return_value=closing_price),
    ):
        r = await client.get("/prices/AAPL")

    assert r.status_code == 200
    data = r.json()
    assert data["market_open"] is False
    assert Decimal(data["price"]) == closing_price
    assert data["next_open_at"] is not None


# ── 4. Order fills at the closing price when the market is closed ──────────────

@pytest.mark.asyncio
async def test_order_fills_at_closing_price_when_market_closed(client):
    """A market buy placed outside NYSE hours fills at the last closing price.

    The adapter is mocked to return the value the online adapter would return
    when yfinance has no live quote: the most recent previous_close.
    """
    closing_price = Decimal("185.50")

    mock_adapter = MagicMock()
    mock_adapter.get_price.return_value = closing_price

    ctx = await create_lobby_http(
        client, "Alice",
        starting_balance="50000",
        asset_universe=["AAPL"],
        fee_pct="0",
        data_source="online",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    alice_id = lb.json()[0]["player_id"]

    with patch("routers.orders.get_adapter", return_value=mock_adapter):
        r = await client.post(
            f"/competitions/{ctx['code']}/players/{alice_id}/orders",
            json={"ticker": "AAPL", "side": "buy", "quantity": "10"},
            headers={"Authorization": f"Bearer {ctx['player_token']}"},
        )

    assert r.status_code == 201
    order = r.json()
    assert order["status"] == "filled"
    assert Decimal(order["fill_price"]) == closing_price
    # Adapter was called with the correct ticker
    mock_adapter.get_price.assert_called_with("AAPL")


@pytest.mark.asyncio
async def test_order_fill_price_is_adapter_price_regardless_of_market_hours(client):
    """fill_price == adapter.get_price() whether the market is open or closed.

    The fill logic never gates on market-open status — it trusts the adapter
    to return the right price (live or last close).
    """
    for label, price in [
        ("live quote (market open)", Decimal("192.30")),
        ("closing price (market closed)", Decimal("185.50")),
    ]:
        mock_adapter = MagicMock()
        mock_adapter.get_price.return_value = price

        ctx = await create_lobby_http(
            client, f"Trader",
            starting_balance="50000",
            asset_universe=["AAPL"],
            fee_pct="0",
            data_source="online",
        )
        await client.post(
            f"/competitions/{ctx['code']}/start",
            headers={"Authorization": f"Bearer {ctx['player_token']}"},
        )
        lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
        player_id = lb.json()[0]["player_id"]

        with patch("routers.orders.get_adapter", return_value=mock_adapter):
            r = await client.post(
                f"/competitions/{ctx['code']}/players/{player_id}/orders",
                json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
                headers={"Authorization": f"Bearer {ctx['player_token']}"},
            )

        assert r.status_code == 201, f"Order failed for {label}: {r.text}"
        assert Decimal(r.json()["fill_price"]) == price, (
            f"fill_price mismatch for {label}: expected {price}"
        )


@pytest.mark.asyncio
async def test_sell_order_fills_at_closing_price_when_market_closed(client):
    """Sell orders also use the adapter's price (last close) when market is closed."""
    buy_price = Decimal("185.50")
    closing_price = Decimal("182.75")  # price dropped slightly after close

    buy_adapter = MagicMock()
    buy_adapter.get_price.return_value = buy_price

    sell_adapter = MagicMock()
    sell_adapter.get_price.return_value = closing_price

    ctx = await create_lobby_http(
        client, "Bob",
        starting_balance="50000",
        asset_universe=["AAPL"],
        fee_pct="0",
        data_source="online",
    )
    await client.post(
        f"/competitions/{ctx['code']}/start",
        headers={"Authorization": f"Bearer {ctx['player_token']}"},
    )
    lb = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    bob_id = lb.json()[0]["player_id"]

    # Buy first (simulate market open)
    with patch("routers.orders.get_adapter", return_value=buy_adapter):
        buy_r = await client.post(
            f"/competitions/{ctx['code']}/players/{bob_id}/orders",
            json={"ticker": "AAPL", "side": "buy", "quantity": "5"},
            headers={"Authorization": f"Bearer {ctx['player_token']}"},
        )
    assert buy_r.status_code == 201
    assert buy_r.json()["status"] == "filled"

    # Sell later (simulate market closed — adapter returns previous_close)
    with patch("routers.orders.get_adapter", return_value=sell_adapter):
        sell_r = await client.post(
            f"/competitions/{ctx['code']}/players/{bob_id}/orders",
            json={"ticker": "AAPL", "side": "sell", "quantity": "5"},
            headers={"Authorization": f"Bearer {ctx['player_token']}"},
        )

    assert sell_r.status_code == 201
    sell_order = sell_r.json()
    assert sell_order["status"] == "filled"
    # The fill price must be the closing price returned by the adapter, not the buy price.
    assert Decimal(sell_order["fill_price"]) == closing_price

    # Realized P&L must be negative: sold below cost basis.
    lb_after = await client.get(f"/competitions/{ctx['code']}/leaderboard")
    bob_entry = lb_after.json()[0]
    assert Decimal(bob_entry["realized_pnl"]) < Decimal("0")
