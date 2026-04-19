import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import settings
from database import Base, engine
from metrics import metrics
from models import alert, watchlist  # ensure tables are registered with Base  # noqa: F401
from routers import alerts, analytics, competitions, orders, players, portfolio, prices, ws
from routers import watchlist as watchlist_router
from services.order_processor import run_order_processor


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    processor_task = asyncio.create_task(
        run_order_processor(settings.ORDER_PROCESSOR_INTERVAL_SECONDS)
    )
    try:
        yield
    finally:
        processor_task.cancel()


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
    return {"status": "ok"}


@app.get("/metrics", tags=["meta"])
async def get_metrics():
    return metrics.summary()
