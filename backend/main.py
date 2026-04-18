from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import settings
from database import Base, engine
from metrics import metrics
from routers import competitions, orders, players, prices


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="TradeHub API", version="0.1.0", lifespan=lifespan)
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
app.include_router(prices.router, prefix="/prices", tags=["prices"])


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}


@app.get("/metrics", tags=["meta"])
async def get_metrics():
    return metrics.summary()
