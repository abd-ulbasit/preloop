"""``resolve_tool_config`` must survive a concurrent first insert.

Two permission checks for a tool the account has never seen both miss the
lookup and both insert. ``uq_account_tool_source`` (NULLS NOT DISTINCT)
rejects the second insert; the loser must adopt the winner's row without
rolling back its session, because a rollback expires the approval workflow
the caller reads next and an async attribute refresh raises
``MissingGreenlet``. These tests drive real ``AsyncSession`` objects.
"""

import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from preloop.models import models
from preloop.models.crud.tool_configuration import (
    get_or_create_tool_configuration_async,
)
from preloop.models.schemas.tool_configuration import ToolConfigurationCreate
from preloop.services.agent_permission_service import (
    AGENT_TOOL_SOURCE,
    resolve_tool_config,
)

LOOKUP = (
    "preloop.models.crud.tool_configuration.get_tool_config_by_name_and_source_async"
)


def _async_url() -> str:
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgresql+psycopg2://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


@asynccontextmanager
async def _engine():
    engine = create_async_engine(_async_url(), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def tenant():
    """A committed account and workflow, removed after the test."""
    async with _engine() as engine:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            account = models.Account(organization_name="Race tenant")
            session.add(account)
            await session.flush()
            workflow = models.ApprovalWorkflow(
                account_id=account.id, name=f"race-{uuid.uuid4()}"
            )
            session.add(workflow)
            await session.commit()
            ids = (account.id, workflow.id)
        yield ids
        async with engine.begin() as conn:
            for table in ("tool_configuration", "approval_workflow"):
                await conn.execute(
                    sa.text(f"DELETE FROM {table} WHERE account_id = :a"),
                    {"a": ids[0]},
                )
            await conn.execute(
                sa.text("DELETE FROM account WHERE id = :a"), {"a": ids[0]}
            )


def _create(account_id, workflow_id) -> ToolConfigurationCreate:
    return ToolConfigurationCreate(
        tool_name="WebFetch",
        tool_source=AGENT_TOOL_SOURCE,
        account_id=str(account_id),
        approval_workflow_id=str(workflow_id),
        is_enabled=True,
        custom_config={},
    )


async def _count(engine, account_id) -> int:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                sa.text(
                    "SELECT count(*) FROM tool_configuration WHERE account_id = :a"
                ),
                {"a": account_id},
            )
        ).scalar()


@pytest.mark.asyncio
async def test_losing_insert_adopts_winner_and_keeps_session_objects(tenant):
    account_id, workflow_id = tenant
    async with _engine() as engine:
        async with AsyncSession(engine, expire_on_commit=False) as winner_db:
            winner = await get_or_create_tool_configuration_async(
                winner_db,
                obj_in=_create(account_id, workflow_id),
                account_id=str(account_id),
            )
        async with AsyncSession(engine, expire_on_commit=False) as loser_db:
            workflow = await loser_db.get(models.ApprovalWorkflow, workflow_id)
            # The loser missed the lookup before the winner committed.
            with patch(LOOKUP, new=AsyncMock(return_value=None)):
                config = await resolve_tool_config(
                    loser_db, str(account_id), "WebFetch", workflow_id
                )

            assert config.id == winner.id
            # Plain sync attribute reads, as create_and_notify does; an
            # expired instance would raise MissingGreenlet here.
            assert workflow.id == workflow_id
            _ = workflow.timeout_seconds
        assert await _count(engine, account_id) == 1


@pytest.mark.asyncio
async def test_first_insert_creates_the_row(tenant):
    account_id, workflow_id = tenant
    async with _engine() as engine:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            config = await resolve_tool_config(
                db, str(account_id), "WebFetch", workflow_id
            )
            again = await resolve_tool_config(
                db, str(account_id), "WebFetch", workflow_id
            )

        assert config.tool_source == AGENT_TOOL_SOURCE
        assert config.approval_workflow_id == workflow_id
        assert again.id == config.id
        assert await _count(engine, account_id) == 1
