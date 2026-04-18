from decimal import Decimal

import pytest

from models.enums import CompetitionState
from schemas.competition import CompetitionCreate, JoinRequest
from services.competition import create_competition, join_competition, start_competition


def make_comp(name: str = "Test", creator: str = "Alice", **kwargs) -> CompetitionCreate:
    defaults: dict = dict(starting_balance=Decimal("10000"), asset_universe=["AAPL", "TSLA"])
    defaults.update(kwargs)
    return CompetitionCreate(name=name, creator_name=creator, **defaults)


@pytest.mark.asyncio
async def test_create_competition(db):
    comp, player, token = await create_competition(db, make_comp())

    assert len(comp.lobby_code) == 6
    assert comp.state == CompetitionState.lobby
    assert player.is_creator is True
    assert player.cash_balance == Decimal("10000")
    assert len(token) > 0


@pytest.mark.asyncio
async def test_lobby_code_is_uppercase_alphanumeric(db):
    comp, _, _ = await create_competition(db, make_comp())
    assert comp.lobby_code.isalnum()
    assert comp.lobby_code == comp.lobby_code.upper()


@pytest.mark.asyncio
async def test_join_competition(db):
    comp, _, _ = await create_competition(db, make_comp(starting_balance=Decimal("5000")))

    player, token = await join_competition(db, comp.lobby_code, JoinRequest(display_name="Bob"))

    assert player.display_name == "Bob"
    assert player.cash_balance == Decimal("5000")
    assert player.is_creator is False
    assert len(token) > 0


@pytest.mark.asyncio
async def test_join_started_competition_fails(db):
    from fastapi import HTTPException

    comp, creator, _ = await create_competition(db, make_comp())
    await start_competition(db, comp.lobby_code, creator)

    with pytest.raises(HTTPException) as exc_info:
        await join_competition(db, comp.lobby_code, JoinRequest(display_name="Late"))
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_start_competition(db):
    comp, creator, _ = await create_competition(db, make_comp())
    updated = await start_competition(db, comp.lobby_code, creator)

    assert updated.state == CompetitionState.active
    assert updated.start_at is not None


@pytest.mark.asyncio
async def test_only_creator_can_start(db):
    from fastapi import HTTPException

    comp, _, _ = await create_competition(db, make_comp())
    _, non_creator, _ = (*await create_competition(db, make_comp(name="Other")),)

    # non_creator belongs to a different competition and is not creator
    with pytest.raises(HTTPException) as exc_info:
        await start_competition(db, comp.lobby_code, non_creator)
    assert exc_info.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_lobby_codes_are_unique(db):
    codes = set()
    for i in range(20):
        comp, _, _ = await create_competition(db, make_comp(name=f"Comp {i}", creator=f"P{i}"))
        codes.add(comp.lobby_code)
    assert len(codes) == 20
