"""Tests for market hours detection in OnlineDataAdapter."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from data_adapters.online import MarketStatus, get_market_status

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def et(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET)


# ── Crypto — always open ──────────────────────────────────────────────────────


def test_btc_usd_always_open():
    status = get_market_status("BTC-USD", now=et(2025, 1, 4, 3, 0))
    assert status.is_open is True
    assert status.next_open_at is None


def test_eth_usd_open_on_weekend():
    # Saturday
    status = get_market_status("ETH-USD", now=et(2025, 1, 4, 12, 0))
    assert status.is_open is True


def test_crypto_open_on_holiday():
    # Christmas Day
    status = get_market_status("BTC-USD", now=et(2025, 12, 25, 10, 0))
    assert status.is_open is True


# ── Regular trading hours ─────────────────────────────────────────────────────


def test_stock_open_during_market_hours():
    # Wednesday 11:00 AM ET — well within market hours
    status = get_market_status("AAPL", now=et(2025, 4, 23, 11, 0))
    assert status.is_open is True
    assert status.next_open_at is None


def test_stock_open_at_exact_open():
    status = get_market_status("MSFT", now=et(2025, 4, 23, 9, 30))
    assert status.is_open is True


def test_stock_closed_one_minute_before_open():
    status = get_market_status("TSLA", now=et(2025, 4, 23, 9, 29))
    assert status.is_open is False
    assert "pre-market" in status.status


def test_stock_closed_at_4pm():
    # 4:00 PM ET — market is closed (close is exclusive ≥ 16:00)
    status = get_market_status("NVDA", now=et(2025, 4, 23, 16, 0))
    assert status.is_open is False
    assert "after-hours" in status.status


def test_stock_closed_after_hours():
    status = get_market_status("AAPL", now=et(2025, 4, 23, 18, 0))
    assert status.is_open is False
    assert status.next_open_at is not None


# ── Weekend ───────────────────────────────────────────────────────────────────


def test_stock_closed_saturday():
    # Saturday
    status = get_market_status("AAPL", now=et(2025, 4, 19, 12, 0))
    assert status.is_open is False
    assert "weekend" in status.status


def test_stock_closed_sunday():
    status = get_market_status("AAPL", now=et(2025, 4, 20, 14, 0))
    assert status.is_open is False
    assert "weekend" in status.status


def test_next_open_after_friday_close_is_monday():
    # Friday after close
    status = get_market_status("AAPL", now=et(2025, 4, 25, 17, 0))
    assert status.is_open is False
    assert status.next_open_at is not None
    # Next open should be Monday
    assert status.next_open_at.weekday() == 0  # Monday


# ── Holidays ──────────────────────────────────────────────────────────────────


def test_new_years_day_closed():
    status = get_market_status("AAPL", now=et(2025, 1, 1, 11, 0))
    assert status.is_open is False


def test_independence_day_closed():
    status = get_market_status("AAPL", now=et(2025, 7, 4, 11, 0))
    assert status.is_open is False


def test_christmas_closed():
    status = get_market_status("AAPL", now=et(2025, 12, 25, 11, 0))
    assert status.is_open is False


def test_thanksgiving_closed():
    # 4th Thursday of November 2025 = Nov 27
    status = get_market_status("AAPL", now=et(2025, 11, 27, 11, 0))
    assert status.is_open is False


def test_next_open_after_holiday_is_weekday():
    # Christmas 2025 is a Thursday
    status = get_market_status("AAPL", now=et(2025, 12, 25, 10, 0))
    assert status.is_open is False
    assert status.next_open_at is not None
    assert status.next_open_at.weekday() < 5  # not a weekend


# ── MarketStatus structure ────────────────────────────────────────────────────


def test_market_status_to_dict_open():
    status = get_market_status("AAPL", now=et(2025, 4, 23, 11, 0))
    d = status.to_dict()
    assert d["market_open"] is True
    assert d["next_open_at"] is None
    assert "timezone" in d
    assert "market_status" in d


def test_market_status_to_dict_closed_has_next_open():
    # Saturday
    status = get_market_status("AAPL", now=et(2025, 4, 19, 10, 0))
    d = status.to_dict()
    assert d["market_open"] is False
    assert d["next_open_at"] is not None


def test_crypto_status_to_dict():
    status = get_market_status("BTC-USD")
    d = status.to_dict()
    assert d["market_open"] is True
    assert d["next_open_at"] is None
    assert "UTC" in d["timezone"]


# ── Pre-market next_open_at points to same day ────────────────────────────────


def test_pre_market_next_open_is_today():
    # 7 AM ET on a weekday (not holiday)
    status = get_market_status("AAPL", now=et(2025, 4, 23, 7, 0))
    assert status.is_open is False
    assert status.next_open_at is not None
    assert status.next_open_at.hour == 9
    assert status.next_open_at.minute == 30
    # Same date
    assert status.next_open_at.day == 23
