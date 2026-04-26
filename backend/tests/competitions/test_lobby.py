"""Tests for POST /lobbies and GET /lobbies/{id}."""

import pytest

from tests.conftest import register_http

# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_PAYLOAD = {
    "name": "Friday Night Trading",
    "asset_universe": ["AAPL", "TSLA", "BTC-USD"],
    "starting_balance": "5000.00",
    "max_players": 8,
    "duration_minutes": 60,
}

_ALICE_NAME = "Alice"


async def _post_lobby(client, payload=None, **overrides):
    """Register Alice, then POST /lobbies with auth. Returns response."""
    reg = await register_http(client, _ALICE_NAME)
    body = {**(payload or VALID_PAYLOAD), **overrides}
    return await client.post(
        "/lobbies",
        json=body,
        headers={"Authorization": f"Bearer {reg['token']}"},
    )


# ── POST /lobbies ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_lobby_returns_uuid(client):
    resp = await _post_lobby(client)
    assert resp.status_code == 201
    body = resp.json()
    # Top-level UUID must be present and non-empty
    assert "id" in body
    assert len(body["id"]) == 36  # standard UUID format


@pytest.mark.asyncio
async def test_create_lobby_returns_lobby_code(client):
    resp = await _post_lobby(client)
    body = resp.json()
    code = body["lobby_code"]
    assert len(code) == 6
    assert code.isupper() or code.isalnum()


@pytest.mark.asyncio
async def test_create_lobby_returns_creator_token(client):
    resp = await _post_lobby(client)
    body = resp.json()
    assert "token" in body
    assert len(body["token"]) > 0


@pytest.mark.asyncio
async def test_create_lobby_attributes_in_response(client):
    resp = await _post_lobby(client)
    lobby = resp.json()["lobby"]
    assert lobby["name"] == "Friday Night Trading"
    assert lobby["asset_universe"] == ["AAPL", "TSLA", "BTC-USD"]
    assert float(lobby["starting_balance"]) == 5000.0
    assert lobby["max_players"] == 8
    assert lobby["duration_minutes"] == 60


@pytest.mark.asyncio
async def test_create_lobby_tickers_uppercased(client):
    resp = await _post_lobby(client, asset_universe=["aapl", "tsla"])
    assert resp.status_code == 201
    lobby = resp.json()["lobby"]
    assert lobby["asset_universe"] == ["AAPL", "TSLA"]


@pytest.mark.asyncio
async def test_create_lobby_state_is_lobby(client):
    resp = await _post_lobby(client)
    assert resp.json()["lobby"]["state"] == "lobby"


@pytest.mark.asyncio
async def test_create_lobby_without_duration(client):
    resp = await _post_lobby(client, duration_minutes=None)
    assert resp.status_code == 201
    assert resp.json()["lobby"]["duration_minutes"] is None


@pytest.mark.asyncio
async def test_create_lobby_two_lobbies_have_different_uuids(client):
    r1 = await _post_lobby(client)
    r2 = await _post_lobby(client)
    assert r1.json()["id"] != r2.json()["id"]


@pytest.mark.asyncio
async def test_create_lobby_two_lobbies_have_different_codes(client):
    r1 = await _post_lobby(client)
    r2 = await _post_lobby(client)
    assert r1.json()["lobby_code"] != r2.json()["lobby_code"]


# ── Validation errors ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_lobby_missing_name_returns_422(client):
    reg = await register_http(client, _ALICE_NAME)
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "name"}
    resp = await client.post(
        "/lobbies", json=payload,
        headers={"Authorization": f"Bearer {reg['token']}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_lobby_empty_asset_universe_returns_422(client):
    resp = await _post_lobby(client, asset_universe=[])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_lobby_duplicate_tickers_returns_422(client):
    resp = await _post_lobby(client, asset_universe=["AAPL", "AAPL"])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_lobby_negative_balance_returns_422(client):
    resp = await _post_lobby(client, starting_balance="-100")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_lobby_max_players_below_minimum_returns_422(client):
    resp = await _post_lobby(client, max_players=1)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_lobby_zero_duration_returns_422(client):
    resp = await _post_lobby(client, duration_minutes=0)
    assert resp.status_code == 422


# ── GET /lobbies/{id} ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_lobby_returns_correct_attributes(client):
    create_resp = await _post_lobby(client)
    lobby_id = create_resp.json()["id"]

    get_resp = await client.get(f"/lobbies/{lobby_id}")
    assert get_resp.status_code == 200
    lobby = get_resp.json()
    assert lobby["id"] == lobby_id
    assert lobby["name"] == "Friday Night Trading"
    assert lobby["asset_universe"] == ["AAPL", "TSLA", "BTC-USD"]
    assert float(lobby["starting_balance"]) == 5000.0
    assert lobby["max_players"] == 8
    assert lobby["duration_minutes"] == 60
    assert lobby["state"] == "lobby"


@pytest.mark.asyncio
async def test_get_lobby_uuid_matches_post_response(client):
    create_resp = await _post_lobby(client)
    lobby_id = create_resp.json()["id"]

    get_resp = await client.get(f"/lobbies/{lobby_id}")
    assert get_resp.json()["id"] == lobby_id


@pytest.mark.asyncio
async def test_get_lobby_not_found_returns_404(client):
    resp = await client.get("/lobbies/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_lobby_preserves_lobby_code(client):
    create_resp = await _post_lobby(client)
    body = create_resp.json()
    lobby_id = body["id"]
    expected_code = body["lobby_code"]

    get_resp = await client.get(f"/lobbies/{lobby_id}")
    assert get_resp.json()["lobby_code"] == expected_code
