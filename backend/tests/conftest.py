import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, get_db
from main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    test_engine = create_async_engine(TEST_DB_URL)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ── Service-level helpers (for tests that call services directly) ──────────────

async def make_user(db, display_name: str):
    """Create and persist a User. Returns (user, token)."""
    from services.user import register_user
    return await register_user(db, display_name)


# ── HTTP-level helpers (for tests that use the client fixture) ────────────────

async def register_http(client, display_name: str) -> dict:
    """Register a user via HTTP. Returns {user_id, token, display_name}."""
    resp = await client.post("/users/register", json={"display_name": display_name})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_lobby_http(client, display_name: str, **overrides) -> dict:
    """Register a user, create a lobby, return combined context dict.

    Returns {user_token, player_token, code, lobby_id}.
    """
    reg = await register_http(client, display_name)
    payload = {"name": "Test Lobby", "asset_universe": ["AAPL"], **overrides}
    resp = await client.post(
        "/lobbies",
        json=payload,
        headers={"Authorization": f"Bearer {reg['token']}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {
        "user_token": reg["token"],
        "player_token": data["token"],
        "code": data["lobby_code"],
        "lobby_id": data["id"],
    }


async def join_http(client, code: str, display_name: str, spectator: bool = False) -> dict:
    """Register a user and join a competition. Returns {user_token, player_token, player_id}."""
    reg = await register_http(client, display_name)
    resp = await client.post(
        f"/competitions/{code}/join",
        json={"spectator": spectator},
        headers={"Authorization": f"Bearer {reg['token']}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {
        "user_token": reg["token"],
        "player_token": data["token"],
        "player_id": data["player_id"],
        "display_name": data["display_name"],
    }
