from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_adapter, get_current_player
from models.competition import Competition
from models.player import Player
from models.position import Position
from schemas.player import PlayerPortfolio, PositionOut

router = APIRouter()


@router.get("/{player_id}/portfolio", response_model=PlayerPortfolio)
async def get_portfolio(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot access another player's portfolio")

    result = await db.execute(select(Position).where(Position.player_id == player_id))
    positions = result.scalars().all()

    comp_result = await db.execute(
        select(Competition).where(Competition.id == current_player.competition_id)
    )
    competition = comp_result.scalar_one()
    adapter = get_adapter(competition.data_source)

    position_outs = []
    positions_value = Decimal("0")
    for pos in positions:
        current_price = None
        unrealized_pnl = None
        try:
            current_price = adapter.get_price(pos.ticker)
            unrealized_pnl = (current_price - pos.avg_entry_price) * pos.quantity
            positions_value += current_price * pos.quantity
        except Exception:
            positions_value += pos.avg_entry_price * pos.quantity

        position_outs.append(
            PositionOut(
                id=pos.id,
                ticker=pos.ticker,
                quantity=pos.quantity,
                avg_entry_price=pos.avg_entry_price,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                opened_at=pos.opened_at,
            )
        )

    return PlayerPortfolio(
        player_id=current_player.id,
        display_name=current_player.display_name,
        cash_balance=current_player.cash_balance,
        positions=position_outs,
        total_value=current_player.cash_balance + positions_value,
    )


@router.get("/{player_id}/history")
async def get_portfolio_history(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    if current_player.id != player_id:
        raise HTTPException(status_code=403, detail="Cannot access another player's history")
    # Placeholder — full P&L timeline will be implemented with price snapshot background task
    return {"player_id": player_id, "snapshots": []}
