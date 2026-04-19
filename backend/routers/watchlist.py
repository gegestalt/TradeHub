"""Watchlist endpoints — per-player ticker tracking."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_player
from models.player import Player
from models.watchlist import WatchlistItem

router = APIRouter(prefix="/players/{player_id}/watchlist", tags=["watchlist"])


@router.post("/{ticker}", status_code=201)
async def add_to_watchlist(
    player_id: str,
    ticker: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    t = ticker.upper()
    # Idempotent — silently succeed if already present
    existing = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.player_id == current_player.id,
            WatchlistItem.ticker == t,
        )
    )
    if existing.scalar_one_or_none():
        return {"ticker": t, "added": False}

    item = WatchlistItem(player_id=current_player.id, ticker=t)
    db.add(item)
    await db.flush()
    return {"ticker": t, "added": True}


@router.get("")
async def get_watchlist(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    result = await db.execute(
        select(WatchlistItem)
        .where(WatchlistItem.player_id == current_player.id)
        .order_by(WatchlistItem.added_at.asc())
    )
    items = result.scalars().all()
    return {"tickers": [i.ticker for i in items]}


@router.delete("/{ticker}", status_code=204)
async def remove_from_watchlist(
    player_id: str,
    ticker: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    t = ticker.upper()
    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.player_id == current_player.id,
            WatchlistItem.ticker == t,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"'{t}' not in watchlist")
    await db.delete(item)
