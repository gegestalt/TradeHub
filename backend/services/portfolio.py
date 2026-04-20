from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_adapters.base import DataAdapter
from models.order import Order
from models.player import Player
from models.position import Position
from schemas.portfolio import PortfolioOut, PositionDetail, TradeOut
from services.financials import quantize as _q

_ZERO = Decimal("0")


async def get_portfolio(db: AsyncSession, player: Player, adapter: DataAdapter) -> PortfolioOut:
    result = await db.execute(select(Position).where(Position.player_id == player.id))
    positions = result.scalars().all()

    position_details: list[PositionDetail] = []
    total_positions_value = _ZERO

    for pos in positions:
        try:
            current_price = Decimal(str(adapter.get_price(pos.ticker)))
        except Exception:
            current_price = pos.avg_entry_price

        market_value = _q(current_price * pos.quantity)
        total_positions_value += market_value

        cost_basis = _q(pos.avg_entry_price * abs(pos.quantity))
        if pos.quantity > 0:
            unrealized_pnl = _q(market_value - cost_basis)
            pnl_pct = _q(unrealized_pnl / cost_basis * 100) if cost_basis else _ZERO
            side = "long"
        else:
            # Short position: profit when price falls below entry
            unrealized_pnl = _q(cost_basis - abs(market_value))
            pnl_pct = _q(unrealized_pnl / cost_basis * 100) if cost_basis else _ZERO
            side = "short"

        position_details.append(
            PositionDetail(
                ticker=pos.ticker,
                quantity=pos.quantity,
                avg_entry_price=pos.avg_entry_price,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=pnl_pct,
                side=side,
            )
        )

    total_value = _q(player.cash_balance + total_positions_value)
    total_unrealized = sum((p.unrealized_pnl for p in position_details), _ZERO)

    return PortfolioOut(
        cash_balance=player.cash_balance,
        positions_value=_q(total_positions_value),
        total_value=total_value,
        unrealized_pnl=_q(total_unrealized),
        realized_pnl=player.realized_pnl,
        positions=position_details,
    )


async def get_competition_trades(
    db: AsyncSession, competition_id: str, limit: int = 50
) -> list[TradeOut]:
    """Return the most recent fills across all players in a competition."""
    from models.enums import OrderStatus

    result = await db.execute(
        select(Order, Player)
        .join(Player, Order.player_id == Player.id)
        .where(
            Player.competition_id == competition_id,
            Order.status == OrderStatus.filled,
            Order.fill_at.is_not(None),
        )
        .order_by(Order.fill_at.desc())
        .limit(limit)
    )
    rows = result.all()

    return [
        TradeOut(
            order_id=order.id,
            player_id=player.id,
            display_name=player.display_name,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            fill_price=order.fill_price,
            fee_paid=order.fee_paid,
            fill_at=order.fill_at.isoformat(),
        )
        for order, player in rows
    ]
