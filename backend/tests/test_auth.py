"""Authentication and authorization tests.

Covers:
- Missing / invalid / wrong token → 401
- Player A cannot place, list, or cancel orders for player B
- Player from competition A cannot act in competition B
- Spectator cannot place orders (403, not 401)
"""

import pytest


LOBBY_PAYLOAD = {
    "name": "Auth Test",
    "creator_name": "Alice",
    "asset_universe": ["AAPL"],
    "starting_balance": "10000",
    "data_source": "mock",
}


async def _create_and_start(client, name="Auth Test", creator="Alice"):
    """Helper: create a lobby, start it, return (code, creator_player_id, token)."""
    resp = await client.post(
        "/competitions",
        json={
            "name": name, "creator_name": creator,
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = resp.json()
    code = body["lobby_code"]
    token = body["token"]
    player_id = body["player_id"]
    await client.post(f"/competitions/{code}/start", headers={"Authorization": f"Bearer {token}"})
    return code, player_id, token


async def _create_with_bob(client, name="Auth Test"):
    """Create a competition, add Bob, then start. Returns (code, alice_id, alice_token, bob_id, bob_token)."""
    resp = await client.post(
        "/competitions",
        json={
            "name": name, "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    body = resp.json()
    code = body["lobby_code"]
    alice_token = body["token"]
    alice_id = body["player_id"]

    bob_resp = await client.post(f"/competitions/{code}/join", json={"display_name": "Bob"})
    bob_token = bob_resp.json()["token"]
    bob_id = bob_resp.json()["player_id"]

    await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    return code, alice_id, alice_token, bob_id, bob_token


# ── Missing token ─────────────────────────────────────────────────────────────
# FastAPI returns 422 for missing required headers; 401 for invalid tokens.
# Both are "unauthenticated" — we accept either 4xx response for missing header.


@pytest.mark.asyncio
async def test_place_order_no_token_returns_4xx(client):
    code, player_id, _ = await _create_and_start(client)
    resp = await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_list_orders_no_token_returns_4xx(client):
    code, player_id, _ = await _create_and_start(client)
    resp = await client.get(f"/competitions/{code}/players/{player_id}/orders")
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_start_competition_no_token_returns_4xx(client):
    create_resp = await client.post("/lobbies", json=LOBBY_PAYLOAD)
    code = create_resp.json()["lobby_code"]
    resp = await client.post(f"/competitions/{code}/start")
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_end_competition_no_token_returns_4xx(client):
    code, _, _ = await _create_and_start(client)
    resp = await client.post(f"/competitions/{code}/end")
    assert resp.status_code in (401, 422)


# ── Invalid token ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_place_order_invalid_token_returns_401(client):
    code, player_id, _ = await _create_and_start(client)
    resp = await client.post(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": "Bearer totally_fake_token"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_orders_invalid_token_returns_401(client):
    code, player_id, _ = await _create_and_start(client)
    resp = await client.get(
        f"/competitions/{code}/players/{player_id}/orders",
        headers={"Authorization": "Bearer not_a_real_token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_start_competition_invalid_token_returns_401(client):
    create_resp = await client.post("/lobbies", json=LOBBY_PAYLOAD)
    code = create_resp.json()["lobby_code"]
    resp = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": "Bearer garbage"},
    )
    assert resp.status_code == 401


# ── Cross-player order theft ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_player_b_cannot_place_order_as_player_a(client):
    """Bob uses his own token but puts Alice's player_id in the URL → 403."""
    code, alice_id, alice_token, bob_id, bob_token = await _create_with_bob(client)

    resp = await client.post(
        f"/competitions/{code}/players/{alice_id}/orders",
        headers={"Authorization": f"Bearer {bob_token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_player_b_cannot_list_orders_as_player_a(client):
    """Bob cannot read Alice's order history."""
    code, alice_id, _, _, bob_token = await _create_with_bob(client)

    resp = await client.get(
        f"/competitions/{code}/players/{alice_id}/orders",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert resp.status_code == 403


# ── Cross-competition isolation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_player_cannot_act_in_different_competition(client):
    """Alice (comp A) cannot place orders in competition B, even with a valid token."""
    code_a, alice_id, alice_token = await _create_and_start(client, name="Comp A")
    code_b, _, _ = await _create_and_start(client, name="Comp B", creator="Bob")

    # Alice uses her token but against code_b
    resp = await client.post(
        f"/competitions/{code_b}/players/{alice_id}/orders",
        headers={"Authorization": f"Bearer {alice_token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_player_cannot_list_orders_in_different_competition(client):
    code_a, alice_id, alice_token = await _create_and_start(client, name="Comp A")
    code_b, _, _ = await _create_and_start(client, name="Comp B", creator="Bob")

    resp = await client.get(
        f"/competitions/{code_b}/players/{alice_id}/orders",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert resp.status_code == 403


# ── Spectator authorization ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spectator_order_returns_403_not_401(client):
    """Spectator has a valid token but is forbidden — must return 403, not 401."""
    code, _, _ = await _create_and_start(client)

    spec_resp = await client.post(
        f"/competitions/{code}/join",
        json={"display_name": "Watcher", "spectator": True},
    )
    spec_token = spec_resp.json()["token"]
    spec_id = spec_resp.json()["player_id"]

    resp = await client.post(
        f"/competitions/{code}/players/{spec_id}/orders",
        headers={"Authorization": f"Bearer {spec_token}"},
        json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
    )
    assert resp.status_code == 403
    assert "Spectator" in resp.json()["detail"]


# ── Non-creator start/end ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_creator_cannot_start_competition(client):
    create_resp = await client.post(
        "/competitions",
        json={
            "name": "Start Guard", "creator_name": "Alice",
            "asset_universe": ["AAPL"], "starting_balance": "10000",
            "data_source": "mock",
        },
    )
    code = create_resp.json()["lobby_code"]

    bob_resp = await client.post(f"/competitions/{code}/join", json={"display_name": "Bob"})
    bob_token = bob_resp.json()["token"]

    resp = await client.post(
        f"/competitions/{code}/start",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_creator_cannot_end_competition(client):
    code, _, alice_token, _, bob_token = await _create_with_bob(client)

    resp = await client.post(
        f"/competitions/{code}/end",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert resp.status_code == 403
