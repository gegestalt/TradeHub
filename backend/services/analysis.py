"""Player analysis service.

Derives trading style, techniques, and narrative commentary from a player's
order history within a competition. Callable at any point (mid-game or after end).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.competition import Competition
from models.enums import OrderSide, OrderStatus, OrderType
from models.order import Order
from models.player import Player
from schemas.analysis import PlayerAnalysisSchema, RoundTripSchema


# ── Internal data container for one ticker's round trip ──────────────────────


@dataclass
class _RoundTrip:
    ticker: str
    buy_price: Decimal
    sell_price: Decimal | None
    quantity: Decimal
    realized_pnl: Decimal | None
    return_pct: Decimal | None
    hold_minutes: float | None
    still_open: bool


# ── Public entry point ────────────────────────────────────────────────────────


async def generate_player_analysis(
    db: AsyncSession,
    player: Player,
    competition: Competition,
) -> PlayerAnalysisSchema:
    result = await db.execute(
        select(Order)
        .where(Order.player_id == player.id)
        .order_by(Order.created_at.asc())
    )
    all_orders: list[Order] = result.scalars().all()

    filled = [o for o in all_orders if o.status == OrderStatus.filled]
    cancelled = [o for o in all_orders if o.status == OrderStatus.cancelled]
    expired = [o for o in all_orders if o.status == OrderStatus.expired]

    buys = [o for o in filled if o.side == OrderSide.buy]
    sells = [o for o in filled if o.side == OrderSide.sell]

    tickers_traded = sorted({o.ticker for o in filled})

    round_trips = _compute_round_trips(buys, sells)
    completed_rts = [rt for rt in round_trips if not rt.still_open]
    profitable_rts = [rt for rt in completed_rts if rt.realized_pnl is not None and rt.realized_pnl > 0]

    win_rate = (len(profitable_rts) / len(completed_rts) * 100) if completed_rts else None

    best_rt = max(completed_rts, key=lambda x: x.realized_pnl or Decimal("0"), default=None)
    worst_rt = min(completed_rts, key=lambda x: x.realized_pnl or Decimal("0"), default=None)

    total_fees = sum(o.fee_paid for o in filled if o.fee_paid)

    hold_times = [rt.hold_minutes for rt in completed_rts if rt.hold_minutes is not None]
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else None

    limit_orders = [o for o in all_orders if o.order_type == OrderType.limit]
    stop_orders = [o for o in all_orders if o.order_type == OrderType.stop_loss]
    tp_orders = [o for o in all_orders if o.order_type == OrderType.take_profit]
    oco_orders = [o for o in all_orders if o.oco_pair_id is not None]
    conditional = [o for o in all_orders if o.order_type != OrderType.market]
    conditional_rate = (len(conditional) / len(all_orders) * 100) if all_orders else 0.0

    trading_style = _classify_trading_style(avg_hold, len(filled), len(completed_rts))
    risk_profile = _classify_risk_profile(
        tickers_traded, bool(stop_orders), bool(tp_orders), len(filled)
    )

    universe_size = len(competition.asset_universe)
    diversification = len(tickers_traded) / universe_size if universe_size > 0 else 0.0

    ticker_counts: dict[str, int] = defaultdict(int)
    for o in filled:
        ticker_counts[o.ticker] += 1
    most_traded = max(ticker_counts, key=lambda k: ticker_counts[k]) if ticker_counts else None

    techniques = _detect_techniques(
        all_orders=all_orders,
        filled=filled,
        round_trips=round_trips,
        tickers_traded=tickers_traded,
        avg_hold=avg_hold,
        used_stop=bool(stop_orders),
        used_tp=bool(tp_orders),
        used_oco=bool(oco_orders),
        used_limit=bool(limit_orders),
    )

    commentary, strengths, improvements = _generate_narrative(
        player=player,
        competition=competition,
        trading_style=trading_style,
        risk_profile=risk_profile,
        techniques=techniques,
        win_rate=win_rate,
        total_fees=total_fees,
        filled_count=len(filled),
        completed_rts=completed_rts,
        diversification=diversification,
        avg_hold=avg_hold,
        used_stop=bool(stop_orders),
        used_tp=bool(tp_orders),
    )

    return PlayerAnalysisSchema(
        player_id=player.id,
        competition_id=competition.id,
        generated_at=datetime.utcnow(),
        total_orders_placed=len(all_orders),
        orders_filled=len(filled),
        orders_cancelled=len(cancelled),
        orders_expired=len(expired),
        round_trips_completed=len(completed_rts),
        total_realized_pnl=player.realized_pnl,
        total_fees_paid=total_fees,
        best_trade_pnl=best_rt.realized_pnl if best_rt else None,
        best_trade_ticker=best_rt.ticker if best_rt else None,
        worst_trade_pnl=worst_rt.realized_pnl if worst_rt else None,
        worst_trade_ticker=worst_rt.ticker if worst_rt else None,
        win_rate_pct=round(win_rate, 1) if win_rate is not None else None,
        avg_hold_minutes=round(avg_hold, 1) if avg_hold is not None else None,
        trading_style=trading_style,
        risk_profile=risk_profile,
        techniques_identified=techniques,
        tickers_traded=tickers_traded,
        most_traded_ticker=most_traded,
        diversification_score=round(diversification, 2),
        used_limit_orders=bool(limit_orders),
        used_stop_loss=bool(stop_orders),
        used_take_profit=bool(tp_orders),
        used_oco=bool(oco_orders),
        conditional_order_rate_pct=round(conditional_rate, 1),
        commentary=commentary,
        strengths=strengths,
        improvements=improvements,
        round_trips=[
            RoundTripSchema(
                ticker=rt.ticker,
                buy_price=rt.buy_price,
                sell_price=rt.sell_price,
                quantity=rt.quantity,
                realized_pnl=rt.realized_pnl,
                return_pct=rt.return_pct,
                hold_minutes=round(rt.hold_minutes, 1) if rt.hold_minutes is not None else None,
                still_open=rt.still_open,
            )
            for rt in round_trips
        ],
    )


# ── Round-trip computation (per-ticker FIFO aggregation) ─────────────────────


def _compute_round_trips(
    buys: list[Order], sells: list[Order]
) -> list[_RoundTrip]:
    buys_by_ticker: dict[str, list[Order]] = defaultdict(list)
    sells_by_ticker: dict[str, list[Order]] = defaultdict(list)

    for o in sorted(buys, key=lambda x: x.fill_at or datetime.min):
        buys_by_ticker[o.ticker].append(o)
    for o in sorted(sells, key=lambda x: x.fill_at or datetime.min):
        sells_by_ticker[o.ticker].append(o)

    all_tickers = set(list(buys_by_ticker.keys()) + list(sells_by_ticker.keys()))
    round_trips: list[_RoundTrip] = []

    for ticker in all_tickers:
        ticker_buys = buys_by_ticker[ticker]
        ticker_sells = sells_by_ticker[ticker]

        if not ticker_buys:
            continue  # short-only, skip

        total_buy_qty = sum(o.quantity for o in ticker_buys)
        avg_buy_price = (
            sum(o.fill_price * o.quantity for o in ticker_buys if o.fill_price)
            / total_buy_qty
        )

        if ticker_sells:
            total_sell_qty = sum(o.quantity for o in ticker_sells)
            avg_sell_price = (
                sum(o.fill_price * o.quantity for o in ticker_sells if o.fill_price)
                / total_sell_qty
            )
            matched_qty = min(total_buy_qty, total_sell_qty)
            realized_pnl = (avg_sell_price - avg_buy_price) * matched_qty
            return_pct = (
                (avg_sell_price - avg_buy_price) / avg_buy_price * Decimal("100")
                if avg_buy_price
                else Decimal("0")
            )
            first_buy_at = min((o.fill_at for o in ticker_buys if o.fill_at), default=None)
            last_sell_at = max((o.fill_at for o in ticker_sells if o.fill_at), default=None)
            hold_minutes = (
                (last_sell_at - first_buy_at).total_seconds() / 60
                if first_buy_at and last_sell_at
                else None
            )
            still_open = total_sell_qty < total_buy_qty
            round_trips.append(
                _RoundTrip(
                    ticker=ticker,
                    buy_price=avg_buy_price,
                    sell_price=avg_sell_price,
                    quantity=matched_qty,
                    realized_pnl=realized_pnl,
                    return_pct=return_pct,
                    hold_minutes=hold_minutes,
                    still_open=still_open,
                )
            )
        else:
            round_trips.append(
                _RoundTrip(
                    ticker=ticker,
                    buy_price=avg_buy_price,
                    sell_price=None,
                    quantity=total_buy_qty,
                    realized_pnl=None,
                    return_pct=None,
                    hold_minutes=None,
                    still_open=True,
                )
            )

    return round_trips


# ── Classification helpers ────────────────────────────────────────────────────


def _classify_trading_style(
    avg_hold_minutes: float | None,
    filled_count: int,
    completed_round_trips: int,
) -> str:
    if filled_count == 0:
        return "inactive"
    if completed_round_trips == 0:
        return "buy_and_hold"
    if avg_hold_minutes is None:
        return "buy_and_hold"
    if avg_hold_minutes < 10:
        return "scalper"
    if avg_hold_minutes < 120:
        return "day_trader"
    if avg_hold_minutes < 2880:  # 2 days
        return "swing_trader"
    return "buy_and_hold"


def _classify_risk_profile(
    tickers_traded: list[str],
    used_stop: bool,
    used_tp: bool,
    total_orders: int,
) -> str:
    risk_points = 0

    # Concentrated = higher risk
    if len(tickers_traded) <= 1:
        risk_points += 2
    elif len(tickers_traded) == 2:
        risk_points += 1

    # No protective orders = higher risk
    if not used_stop and not used_tp:
        risk_points += 1

    # Very high activity = higher risk
    if total_orders > 20:
        risk_points += 1

    if risk_points <= 1:
        return "conservative"
    if risk_points <= 2:
        return "moderate"
    return "aggressive"


# ── Technique detection ───────────────────────────────────────────────────────


def _detect_techniques(
    all_orders: list[Order],
    filled: list[Order],
    round_trips: list[_RoundTrip],
    tickers_traded: list[str],
    avg_hold: float | None,
    used_stop: bool,
    used_tp: bool,
    used_oco: bool,
    used_limit: bool,
) -> list[str]:
    techniques: list[str] = []

    # Risk management tools
    if used_oco:
        techniques.append("OCO (One-Cancels-Other) bracket orders")
    elif used_stop and used_tp:
        techniques.append("Full bracket management (stop-loss + take-profit)")
    elif used_stop:
        techniques.append("Stop-loss risk management")
    elif used_tp:
        techniques.append("Take-profit targeting")

    if used_limit:
        techniques.append("Limit order execution (price discipline)")

    # Diversification vs concentration
    if len(tickers_traded) >= 4:
        techniques.append("Portfolio diversification")
    elif len(tickers_traded) == 1 and filled:
        techniques.append("Single-asset concentration")

    # Trading style
    if avg_hold is not None:
        if avg_hold < 10:
            techniques.append("Scalping (sub-10-minute holds)")
        elif avg_hold < 120:
            techniques.append("Intraday trading")
        elif avg_hold >= 2880:
            techniques.append("Multi-day position holding")

    # Detect averaging down and pyramiding per ticker
    buys_by_ticker: dict[str, list[Order]] = defaultdict(list)
    sells_by_ticker: dict[str, list[Order]] = defaultdict(list)
    for o in filled:
        if o.side == OrderSide.buy:
            buys_by_ticker[o.ticker].append(o)
        else:
            sells_by_ticker[o.ticker].append(o)

    for ticker, ticker_buys in buys_by_ticker.items():
        if len(ticker_buys) < 2:
            continue

        sorted_buys = sorted(ticker_buys, key=lambda x: x.fill_at or datetime.min)
        ticker_sells = sells_by_ticker.get(ticker, [])
        first_sell_at = (
            min(o.fill_at for o in ticker_sells if o.fill_at)
            if ticker_sells
            else None
        )

        # Only look at buys before the first sell
        pre_sell_buys = [
            b for b in sorted_buys
            if first_sell_at is None or (b.fill_at and b.fill_at < first_sell_at)
        ]
        if len(pre_sell_buys) < 2:
            continue

        prices = [b.fill_price for b in pre_sell_buys if b.fill_price]
        if len(prices) < 2:
            continue

        if prices[-1] < prices[0]:
            techniques.append(f"Position averaging / dip-buying ({ticker})")
        elif prices[-1] > prices[0]:
            techniques.append(f"Pyramiding into winner ({ticker})")

    # Profit locking: sold before end of competition
    if any(not rt.still_open for rt in round_trips):
        techniques.append("Profit locking (closed positions)")

    # Fully passive: only buys, no sells
    if filled and not any(o.side == OrderSide.sell for o in filled):
        techniques.append("Buy-and-hold (never sold)")

    return list(dict.fromkeys(techniques))  # deduplicate, preserve order


# ── Narrative generation ──────────────────────────────────────────────────────


def _generate_narrative(
    player: Player,
    competition: Competition,
    trading_style: str,
    risk_profile: str,
    techniques: list[str],
    win_rate: float | None,
    total_fees: Decimal,
    filled_count: int,
    completed_rts: list[_RoundTrip],
    diversification: float,
    avg_hold: float | None,
    used_stop: bool,
    used_tp: bool,
) -> tuple[str, list[str], list[str]]:
    name = player.display_name
    pnl = player.realized_pnl

    if filled_count == 0:
        commentary = (
            f"{name} did not execute any trades during this competition. "
            "No market activity means no P&L data or technique analysis is available."
        )
        return commentary, ["Participated in the competition"], [
            "Place trades to gain hands-on market experience",
            "Explore the asset universe and practice order types",
        ]

    style_labels = {
        "scalper": "an active scalper, targeting very short-term price movements",
        "day_trader": "a day trader, opening and closing positions within hours",
        "swing_trader": "a swing trader, holding positions across multiple hours or days",
        "buy_and_hold": "a buy-and-hold investor, accumulating positions without selling",
        "inactive": "a low-activity participant",
    }
    style_desc = style_labels.get(trading_style, trading_style)

    parts: list[str] = [f"{name} traded as {style_desc}."]

    if avg_hold is not None:
        if avg_hold < 5:
            parts.append("Positions were held for under 5 minutes on average — classic scalping cadence.")
        elif avg_hold < 60:
            parts.append(f"Average hold time was {avg_hold:.0f} minutes.")
        elif avg_hold < 1440:
            parts.append(f"Average hold time was {avg_hold / 60:.1f} hours.")
        else:
            parts.append(f"Average hold time was {avg_hold / 1440:.1f} day(s).")

    if completed_rts:
        wrd = f"{win_rate:.0f}%" if win_rate is not None else "unknown"
        parts.append(
            f"Completed {len(completed_rts)} ticker round trip(s) with a {wrd} win rate."
        )

    if techniques:
        parts.append(f"Techniques identified: {', '.join(techniques)}.")

    pnl_sign = "+" if pnl >= 0 else ""
    parts.append(
        f"Total realized P&L: {pnl_sign}{float(pnl):.2f} "
        f"(fees paid: {float(total_fees):.2f})."
    )

    commentary = " ".join(parts)

    # Strengths
    strengths: list[str] = []
    if used_stop:
        strengths.append("Used stop-loss orders — shows awareness of downside risk")
    if used_tp:
        strengths.append("Set take-profit targets — disciplined approach to locking gains")
    if diversification > 0.5:
        strengths.append("Good diversification — spread across multiple assets in the universe")
    if win_rate is not None and win_rate >= 60:
        strengths.append(f"Strong win rate of {win_rate:.0f}% on closed positions")
    if pnl > 0:
        strengths.append(f"Generated positive realized P&L of +{float(pnl):.2f}")
    if filled_count >= 5:
        strengths.append("Active market participation with multiple trades")

    if not strengths:
        strengths.append("Participated and executed trades")

    # Improvements
    improvements: list[str] = []
    if not used_stop and filled_count > 0:
        improvements.append(
            "Consider stop-loss orders to limit downside on open positions"
        )
    if win_rate is not None and win_rate < 40:
        improvements.append(
            f"Win rate of {win_rate:.0f}% is low — review entry criteria and timing"
        )
    if diversification < 0.3 and len(competition.asset_universe) > 2:
        improvements.append(
            "Portfolio is highly concentrated — spreading across more assets reduces single-asset risk"
        )
    if total_fees > abs(pnl) * Decimal("0.1") and pnl != 0:
        improvements.append(
            "Fees represent a large share of returns — fewer, larger trades can improve net P&L"
        )

    return commentary, strengths, improvements
