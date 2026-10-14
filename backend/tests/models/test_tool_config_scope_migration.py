"""Run the tool-configuration scope migration against a real Postgres DB.

The migration folds duplicate account-wide rows (a production artefact of a
get-or-create race under a NULLs-distinct unique constraint) into the oldest
row and recreates ``uq_account_tool_source`` so the account-wide scope is
unique as well. These tests seed the duplicate shape, run ``upgrade()`` and
check both the fold and the tightened constraint.
"""

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "preloop"
    / "models"
    / "alembic"
    / "versions"
    / "20261014_tool_config_scope_nulls.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "tool_config_scope_nulls_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(db_session) -> None:
    migration = _load_migration()
    context = MigrationContext.configure(db_session.connection())
    with Operations.context(context):
        migration.upgrade()


def _insert_config(db_session, account_id, *, created_at, **scope) -> uuid.UUID:
    config_id = uuid.uuid4()
    db_session.execute(
        sa.text(
            "INSERT INTO tool_configuration (id, account_id, tool_name, "
            "tool_source, mcp_server_id, managed_agent_id, is_enabled, "
            "custom_config, created_at, updated_at) VALUES (:id, :account_id, "
            "'WebFetch', 'agent', :mcp_server_id, :managed_agent_id, true, "
            "'{}'::jsonb, :created_at, :created_at)"
        ),
        {
            "id": config_id,
            "account_id": account_id,
            "mcp_server_id": scope.get("mcp_server_id"),
            "managed_agent_id": scope.get("managed_agent_id"),
            "created_at": created_at,
        },
    )
    return config_id


def _insert_rule(db_session, account_id, config_id) -> uuid.UUID:
    rule_id = uuid.uuid4()
    db_session.execute(
        sa.text(
            "INSERT INTO tool_access_rules (id, account_id, "
            "tool_configuration_id, condition_type, action, priority, "
            "created_at, updated_at) VALUES (:id, :account_id, :config_id, "
            "'simple', 'deny', 0, now(), now())"
        ),
        {"id": rule_id, "account_id": account_id, "config_id": config_id},
    )
    return rule_id


def _insert_request(db_session, account_id, config_id) -> uuid.UUID:
    from preloop.models import models

    workflow = models.ApprovalWorkflow(account_id=account_id, name=str(uuid.uuid4()))
    db_session.add(workflow)
    db_session.flush()
    workflow_id = workflow.id
    request_id = uuid.uuid4()
    db_session.execute(
        sa.text(
            "INSERT INTO approval_request (id, account_id, tool_configuration_id, "
            "approval_workflow_id, tool_name, tool_args, status, requested_at, "
            "approval_token, created_at, updated_at) VALUES (:id, :account_id, "
            ":config_id, :workflow_id, 'WebFetch', '{}'::jsonb, 'approved', now(), "
            ":token, now(), now())"
        ),
        {
            "id": request_id,
            "account_id": account_id,
            "config_id": config_id,
            "workflow_id": workflow_id,
            "token": uuid.uuid4().hex,
        },
    )
    return request_id


def _config_ids(db_session, account_id) -> list[uuid.UUID]:
    rows = db_session.execute(
        sa.text(
            "SELECT id FROM tool_configuration WHERE account_id = :account_id "
            "ORDER BY created_at"
        ),
        {"account_id": account_id},
    )
    return [row[0] for row in rows]


@pytest.fixture
def account_id(db_session) -> uuid.UUID:
    from preloop.models import models

    account = models.Account(organization_name="Scope migration tenant")
    db_session.add(account)
    db_session.flush()
    return account.id


@pytest.fixture
def nulls_distinct_constraint(db_session) -> None:
    """Restore the pre-migration constraint so duplicates can be seeded."""
    db_session.execute(
        sa.text("ALTER TABLE tool_configuration DROP CONSTRAINT uq_account_tool_source")
    )
    db_session.execute(
        sa.text(
            "ALTER TABLE tool_configuration ADD CONSTRAINT uq_account_tool_source "
            "UNIQUE (account_id, tool_name, tool_source, mcp_server_id, "
            "managed_agent_id)"
        )
    )


def test_upgrade_folds_duplicates_into_the_oldest_row(
    db_session, account_id, nulls_distinct_constraint
):
    base = datetime(2026, 7, 14, 13, 50, 59, tzinfo=timezone.utc)
    keeper = _insert_config(db_session, account_id, created_at=base)
    loser = _insert_config(
        db_session, account_id, created_at=base + timedelta(milliseconds=1)
    )
    rule_id = _insert_rule(db_session, account_id, loser)
    request_id = _insert_request(db_session, account_id, loser)
    assert _config_ids(db_session, account_id) == [keeper, loser]

    _run_upgrade(db_session)

    assert _config_ids(db_session, account_id) == [keeper]
    moved_to = db_session.execute(
        sa.text("SELECT tool_configuration_id FROM tool_access_rules WHERE id = :id"),
        {"id": rule_id},
    ).scalar()
    assert moved_to == keeper
    request_moved_to = db_session.execute(
        sa.text("SELECT tool_configuration_id FROM approval_request WHERE id = :id"),
        {"id": request_id},
    ).scalar()
    assert request_moved_to == keeper


def test_upgrade_keeps_scoped_rows_and_rejects_new_account_wide_duplicates(
    db_session, account_id, nulls_distinct_constraint
):
    from preloop.models import models

    agent = models.ManagedAgent(
        id=uuid.uuid4(),
        account_id=account_id,
        agent_kind="claude_code",
        session_source_type="claude_code",
        session_source_id="host-1",
        display_name="Claude Code (laptop)",
        enrolled_via="runtime_session_token",
        lifecycle_state="active",
        lifecycle_updated_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    db_session.add(agent)
    db_session.flush()
    base = datetime(2026, 7, 14, 13, 50, 59, tzinfo=timezone.utc)
    account_wide = _insert_config(db_session, account_id, created_at=base)
    scoped = _insert_config(
        db_session,
        account_id,
        created_at=base + timedelta(seconds=1),
        managed_agent_id=agent.id,
    )

    _run_upgrade(db_session)

    assert _config_ids(db_session, account_id) == [account_wide, scoped]
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _insert_config(
                db_session, account_id, created_at=base + timedelta(seconds=2)
            )


def test_upgrade_is_idempotent(db_session, account_id):
    base = datetime(2026, 7, 14, 13, 50, 59, tzinfo=timezone.utc)
    only = _insert_config(db_session, account_id, created_at=base)

    _run_upgrade(db_session)
    _run_upgrade(db_session)

    assert _config_ids(db_session, account_id) == [only]
