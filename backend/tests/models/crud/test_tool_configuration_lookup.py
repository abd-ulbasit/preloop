"""Name-keyed tool configuration lookups against a real Postgres DB.

``uq_account_tool_source`` lets an account-wide row coexist with rows scoped to
one managed agent or one MCP server for the same tool. Lookups keyed only by
name (and source) must prefer the account-wide row instead of raising
``MultipleResultsFound``, which is what turned the agent permission check into
a 500 in production.
"""

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from preloop.models import models
from preloop.models.crud.tool_configuration import (
    get_tool_config_by_name_and_source_async,
    get_tool_config_by_tool_name_async,
)


@asynccontextmanager
async def _async_session():
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgresql+psycopg2://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix) :]
            break
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


def _config(account_id, *, tool_source, managed_agent_id=None):
    return models.ToolConfiguration(
        account_id=account_id,
        tool_name="WebFetch",
        tool_source=tool_source,
        managed_agent_id=managed_agent_id,
        is_enabled=True,
        custom_config={},
    )


async def _seed(session):
    account = models.Account(organization_name="Lookup tenant")
    session.add(account)
    await session.flush()
    agent = models.ManagedAgent(
        id=uuid.uuid4(),
        account_id=account.id,
        agent_kind="claude_code",
        session_source_type="claude_code",
        session_source_id="host-1",
        display_name="Claude Code (laptop)",
        enrolled_via="runtime_session_token",
        lifecycle_state="active",
        lifecycle_updated_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(agent)
    await session.flush()
    # The scoped row is inserted first so insertion order cannot mask a wrong
    # preference; the MCP-source row shares the name across sources.
    scoped = _config(account.id, tool_source="agent", managed_agent_id=agent.id)
    session.add(scoped)
    await session.flush()
    mcp = _config(account.id, tool_source="mcp")
    session.add(mcp)
    await session.flush()
    account_wide = _config(account.id, tool_source="agent")
    session.add(account_wide)
    await session.flush()
    return account, scoped, mcp, account_wide


@pytest.mark.asyncio
async def test_name_and_source_lookup_prefers_the_account_wide_row():
    async with _async_session() as session:
        account, scoped, _mcp, account_wide = await _seed(session)

        found = await get_tool_config_by_name_and_source_async(
            session,
            account_id=str(account.id),
            tool_name="WebFetch",
            tool_source="agent",
        )

        assert found is not None
        assert found.id == account_wide.id
        assert found.id != scoped.id


@pytest.mark.asyncio
async def test_name_lookup_across_sources_returns_one_row():
    async with _async_session() as session:
        account, _scoped, mcp, _account_wide = await _seed(session)

        found = await get_tool_config_by_tool_name_async(
            session, account_id=str(account.id), tool_name="WebFetch"
        )

        assert found is not None
        assert found.managed_agent_id is None
        assert found.id in {mcp.id, _account_wide.id}


@pytest.mark.asyncio
async def test_name_and_source_lookup_returns_none_when_absent():
    async with _async_session() as session:
        account, *_ = await _seed(session)

        found = await get_tool_config_by_name_and_source_async(
            session,
            account_id=str(account.id),
            tool_name="Missing",
            tool_source="agent",
        )

        assert found is None
