import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import settings
from database import Base, engine, get_db
from metrics import metrics
from models import alert, portfolio_snapshot, user, watchlist  # register tables  # noqa: F401
from routers import (
    alerts,
    analytics,
    assets,
    competitions,
    lobbies,
    orders,
    players,
    portfolio,
    prices,
    trading_screen,
    users,
    ws,
)
from routers import watchlist as watchlist_router
from services.order_processor import run_order_processor
from services.snapshot_task import run_snapshot_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    processor_task = asyncio.create_task(
        run_order_processor(settings.ORDER_PROCESSOR_INTERVAL_SECONDS)
    )
    snapshot_task = asyncio.create_task(
        run_snapshot_task(settings.PRICE_SNAPSHOT_INTERVAL_SECONDS)
    )
    try:
        yield
    finally:
        processor_task.cancel()
        snapshot_task.cancel()


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="TradeHub API", version="0.4.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(assets.router)
app.include_router(trading_screen.router)
app.include_router(lobbies.router)
app.include_router(competitions.router, prefix="/competitions", tags=["competitions"])
app.include_router(players.router, prefix="/players", tags=["players"])
app.include_router(orders.router, tags=["orders"])
app.include_router(portfolio.router, tags=["portfolio"])
app.include_router(prices.router, prefix="/prices", tags=["prices"])
app.include_router(analytics.router, tags=["analytics"])
app.include_router(alerts.router)
app.include_router(watchlist_router.router)
app.include_router(ws.router, tags=["websocket"])


@app.get("/health", tags=["meta"])
async def health():
    from services.cache import price_cache
    return {
        "status": "ok",
        "cache_backend": price_cache.backend,
        "cache_entries": price_cache.size,
    }


@app.get("/metrics", tags=["meta"])
async def get_metrics():
    return metrics.summary()


@app.get("/competitions/{code}/ledger", tags=["audit"])
async def get_ledger(
    code: str,
    player_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return all ledger entries for a competition (optionally filtered by player).

    Each entry is one side of a debit/credit pair. Use /ledger/invariant to
    verify the global balance is intact.
    """
    from sqlalchemy import select as sa_select
    from models.audit_log import AuditLog
    from models.ledger_entry import LedgerEntry
    from models.competition import Competition

    result = await db.execute(
        sa_select(Competition).where(Competition.lobby_code == code)
    )
    competition = result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail=f"Competition '{code}' not found")

    stmt = sa_select(LedgerEntry).where(
        LedgerEntry.competition_id == competition.id
    ).order_by(LedgerEntry.recorded_at)
    if player_id:
        stmt = stmt.where(LedgerEntry.player_id == player_id)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "journal_id": r.journal_id,
            "recorded_at": r.recorded_at.isoformat(),
            "event_type": r.account_type,
            "side": r.side,
            "amount": str(r.amount),
            "player_id": r.player_id,
            "order_id": r.order_id,
            "ticker": r.ticker,
            "description": r.description,
        }
        for r in rows
    ]


@app.get("/competitions/{code}/ledger/invariant", tags=["audit"])
async def check_ledger_invariant(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """Verify that total debits == total credits for this competition.

    A non-zero delta means funds were created or destroyed — a critical error
    that should trigger immediate investigation.
    """
    from sqlalchemy import select as sa_select
    from models.competition import Competition
    from services.ledger import check_invariant

    result = await db.execute(
        sa_select(Competition).where(Competition.lobby_code == code)
    )
    competition = result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail=f"Competition '{code}' not found")

    result = await check_invariant(db, competition_id=competition.id)
    return {
        "competition": code,
        "balanced": result["balanced"],
        "debit_total": str(result["debit_total"]),
        "credit_total": str(result["credit_total"]),
        "delta": str(result["delta"]),
        "status": "ok" if result["balanced"] else "CRITICAL: ledger imbalanced",
    }


@app.get("/competitions/{code}/audit", tags=["audit"])
async def get_audit_log(
    code: str,
    player_id: str | None = None,
    event_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return the structured audit log for a competition.

    Every order fill, rejection, and position change is recorded here
    atomically alongside the balance update.
    """
    from sqlalchemy import select as sa_select
    from models.audit_log import AuditLog
    from models.competition import Competition

    result = await db.execute(
        sa_select(Competition).where(Competition.lobby_code == code)
    )
    competition = result.scalar_one_or_none()
    if not competition:
        raise HTTPException(status_code=404, detail=f"Competition '{code}' not found")

    stmt = sa_select(AuditLog).where(
        AuditLog.competition_id == competition.id
    ).order_by(AuditLog.recorded_at)
    if player_id:
        stmt = stmt.where(AuditLog.player_id == player_id)
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "recorded_at": r.recorded_at.isoformat(),
            "event_type": r.event_type,
            "player_id": r.player_id,
            "order_id": r.order_id,
            "ticker": r.ticker,
            "payload": r.payload,
        }
        for r in rows
    ]
