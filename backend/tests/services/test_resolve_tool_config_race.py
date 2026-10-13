"""``resolve_tool_config`` must survive a concurrent first insert.

Two permission checks for a tool the account has never seen both miss the
lookup and both insert. The unique constraint now rejects the loser, which
must adopt the winner's row instead of failing the hook.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from preloop.services.agent_permission_service import resolve_tool_config

LOOKUP = (
    "preloop.models.crud.tool_configuration.get_tool_config_by_name_and_source_async"
)
CREATE = "preloop.models.crud.tool_configuration.create_tool_configuration_async"


def _integrity_error() -> IntegrityError:
    return IntegrityError("INSERT INTO tool_configuration", {}, Exception("dup"))


@pytest.mark.asyncio
async def test_losing_insert_adopts_the_winning_row():
    db = AsyncMock()
    winner = MagicMock(id=uuid.uuid4())
    lookup = AsyncMock(side_effect=[None, winner])
    create = AsyncMock(side_effect=_integrity_error())

    with patch(LOOKUP, new=lookup), patch(CREATE, new=create):
        config = await resolve_tool_config(db, "acct", "WebFetch", uuid.uuid4())

    assert config is winner
    db.rollback.assert_awaited_once()
    assert lookup.await_count == 2
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_integrity_error_without_a_winner_is_raised():
    db = AsyncMock()
    lookup = AsyncMock(side_effect=[None, None])
    create = AsyncMock(side_effect=_integrity_error())

    with patch(LOOKUP, new=lookup), patch(CREATE, new=create):
        with pytest.raises(IntegrityError):
            await resolve_tool_config(db, "acct", "WebFetch", uuid.uuid4())

    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_row_skips_the_insert():
    db = AsyncMock()
    existing = MagicMock(id=uuid.uuid4())
    create = AsyncMock()

    with patch(LOOKUP, new=AsyncMock(return_value=existing)), patch(CREATE, new=create):
        config = await resolve_tool_config(db, "acct", "WebFetch", uuid.uuid4())

    assert config is existing
    create.assert_not_awaited()
