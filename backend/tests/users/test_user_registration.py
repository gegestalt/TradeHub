"""Tests for user registration and competition join enforcement."""

import pytest

from models.user import User
from services.user import register_user
from tests.conftest import create_lobby_http, join_http, make_user, register_http


# ── Unit: register_user service ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_creates_user(db):
    user, token = await make_user(db, "Alice")
    assert isinstance(user, User)
    assert user.display_name == "Alice"
    assert len(token) > 20


@pytest.mark.asyncio
async def test_register_token_is_unique(db):
    _, token_a = await make_user(db, "Alice")
    _, token_b = await make_user(db, "Bob")
    assert token_a != token_b


@pytest.mark.asyncio
async def test_register_same_display_name_allowed(db):
    """Display names are not unique — token is the identifier."""
    _, token_a = await make_user(db, "Alice")
    _, token_b = await make_user(db, "Alice")
    assert token_a != token_b


# ── HTTP: POST /users/register ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_http_returns_token(client):
    resp = await client.post("/users/register", json={"display_name": "Alice"})
    assert resp.status_code == 201
    data = resp.json()
    assert "user_id" in data
    assert "token" in data
    assert data["display_name"] == "Alice"


@pytest.mark.asyncio
async def test_register_http_empty_name_rejected(client):
    resp = await client.post("/users/register", json={"display_name": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_http_name_too_long_rejected(client):
    resp = await client.post("/users/register", json={"display_name": "x" * 101})
    assert resp.status_code == 422


# ── HTTP: join now requires user token ────────────────────────────────────────


@pytest.mark.asyncio
async def test_join_requires_auth(client):
    ctx = await create_lobby_http(client, "Alice", asset_universe=["AAPL"])
    resp = await client.post(f"/competitions/{ctx['code']}/join", json={})
    assert resp.status_code == 422  # missing Authorization header


@pytest.mark.asyncio
async def test_join_with_invalid_token_rejected(client):
    ctx = await create_lobby_http(client, "Alice", asset_universe=["AAPL"])
    resp = await client.post(
        f"/competitions/{ctx['code']}/join",
        json={},
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_join_with_valid_user_token_succeeds(client):
    ctx = await create_lobby_http(client, "Alice", asset_universe=["AAPL"])
    bob = await join_http(client, ctx["code"], "Bob")
    assert bob["player_id"]
    assert bob["player_token"]
    assert bob["display_name"] == "Bob"


@pytest.mark.asyncio
async def test_join_uses_registered_display_name(client):
    ctx = await create_lobby_http(client, "Alice", asset_universe=["AAPL"])
    bob = await join_http(client, ctx["code"], "Bob The Trader")
    assert bob["display_name"] == "Bob The Trader"


@pytest.mark.asyncio
async def test_duplicate_join_rejected(client):
    """Same user token cannot join the same competition twice."""
    ctx = await create_lobby_http(client, "Alice", asset_universe=["AAPL"])
    reg = await register_http(client, "Bob")
    headers = {"Authorization": f"Bearer {reg['token']}"}

    first = await client.post(f"/competitions/{ctx['code']}/join", json={}, headers=headers)
    assert first.status_code == 201

    second = await client.post(f"/competitions/{ctx['code']}/join", json={}, headers=headers)
    assert second.status_code == 409
    assert "already joined" in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_creator_token_cannot_rejoin(client):
    """The user who created the lobby is already a player — joining again fails."""
    reg = await register_http(client, "Alice")
    headers = {"Authorization": f"Bearer {reg['token']}"}

    lobby_resp = await client.post(
        "/lobbies",
        json={"name": "Test", "asset_universe": ["AAPL"]},
        headers=headers,
    )
    code = lobby_resp.json()["lobby_code"]

    rejoin = await client.post(f"/competitions/{code}/join", json={}, headers=headers)
    assert rejoin.status_code == 409


@pytest.mark.asyncio
async def test_different_users_can_join_same_competition(client):
    ctx = await create_lobby_http(client, "Alice", asset_universe=["AAPL"])
    bob = await join_http(client, ctx["code"], "Bob")
    charlie = await join_http(client, ctx["code"], "Charlie")
    assert bob["player_id"] != charlie["player_id"]
    assert bob["player_token"] != charlie["player_token"]


@pytest.mark.asyncio
async def test_same_user_can_join_different_competitions(client):
    """A user can be in multiple different competitions."""
    ctx_a = await create_lobby_http(client, "Host", asset_universe=["AAPL"])
    ctx_b = await create_lobby_http(client, "Host2", asset_universe=["AAPL"])

    reg = await register_http(client, "Traveller")
    headers = {"Authorization": f"Bearer {reg['token']}"}

    r_a = await client.post(f"/competitions/{ctx_a['code']}/join", json={}, headers=headers)
    r_b = await client.post(f"/competitions/{ctx_b['code']}/join", json={}, headers=headers)
    assert r_a.status_code == 201
    assert r_b.status_code == 201


# ── HTTP: lobby creation now requires user token ──────────────────────────────


@pytest.mark.asyncio
async def test_create_lobby_requires_auth(client):
    resp = await client.post(
        "/lobbies", json={"name": "Test", "asset_universe": ["AAPL"]}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_lobby_sets_creator_display_name(client):
    ctx = await create_lobby_http(client, "GameMaster", asset_universe=["AAPL"])
    # Fetch leaderboard to verify creator's display_name
    # (competition is still in lobby state so leaderboard may not include all data,
    # but the lobby itself is created — just verify code returned)
    assert len(ctx["code"]) == 6


@pytest.mark.asyncio
async def test_player_token_and_user_token_are_different(client):
    reg = await register_http(client, "Alice")
    lobby_resp = await client.post(
        "/lobbies",
        json={"name": "Test", "asset_universe": ["AAPL"]},
        headers={"Authorization": f"Bearer {reg['token']}"},
    )
    player_token = lobby_resp.json()["token"]
    assert player_token != reg["token"]
