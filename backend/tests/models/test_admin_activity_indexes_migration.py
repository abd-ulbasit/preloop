"""The admin activity index migration, and the models that mirror it.

The admin accounts list needs ``ix_event_account_timestamp`` (latest event
per account) and ``ix_api_usage_gateway_spend`` (trailing gateway spend per
account). Two ways to lose them silently are pinned:

* a CONCURRENTLY build that failed part way leaves an INVALID index; the
  migration runner retries, and ``IF NOT EXISTS`` alone would skip the
  rebuild and commit a revision whose index the planner never uses;
* an index the model metadata does not declare is one the next
  ``alembic revision --autogenerate`` proposes to drop.
"""

import importlib.util
from pathlib import Path

import pytest

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from preloop.models.models.base import Base

INDEXES = ("ix_event_account_timestamp", "ix_api_usage_gateway_spend")
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "preloop"
    / "models"
    / "alembic"
    / "versions"
    / "20261013_admin_activity_indexes.py"
)


def _load_migration():
    """Import the migration module by path (its name starts with digits)."""
    spec = importlib.util.spec_from_file_location(
        "admin_activity_indexes_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(migration, connection) -> None:
    """Run ``upgrade()`` the way the migration runner does, per revision.

    ``autocommit_block`` needs a migration transaction to step out of, so the
    connection's implicit one is closed and the context opens its own.
    """
    connection.commit()
    context = MigrationContext.configure(connection)
    with context.begin_transaction(), Operations.context(context):
        migration.upgrade()


def _index_valid(connection, index):
    return connection.execute(
        text(
            "SELECT i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
        ),
        {"name": index},
    ).scalar()


@pytest.mark.parametrize("index", INDEXES)
def test_upgrade_rebuilds_an_invalid_leftover_index(db_engine, index):
    """A retried upgrade must not keep a half-built index.

    CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction, so this
    uses its own autocommit connection instead of the rolled-back test
    session. It leaves the index valid, as it found it.
    """
    migration = _load_migration()
    with db_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        assert _index_valid(connection, index) is True, "run alembic upgrade head"
        # What an interrupted CONCURRENTLY build leaves behind.
        connection.execute(
            text(
                "UPDATE pg_index SET indisvalid = false "
                "WHERE indexrelid = CAST(:name AS regclass)"
            ),
            {"name": index},
        )
        try:
            assert _index_valid(connection, index) is False
            _upgrade(migration, connection)
            assert _index_valid(connection, index) is True
        finally:
            # Never leave the shared test schema with a broken index, even
            # when the assertion above fails.
            with db_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as repair:
                if _index_valid(repair, index) is not True:
                    repair.execute(text(f"REINDEX INDEX {index}"))


def test_upgrade_is_a_no_op_on_valid_indexes(db_engine):
    migration = _load_migration()
    query = text("SELECT CAST(:name AS regclass)::oid")
    with db_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        before = [connection.execute(query, {"name": i}).scalar() for i in INDEXES]
        _upgrade(migration, connection)
        after = [connection.execute(query, {"name": i}).scalar() for i in INDEXES]
    assert after == before


def test_model_metadata_declares_the_indexes(db_session):
    """Autogenerate must not see the migrated index as one to drop."""
    context = MigrationContext.configure(db_session.connection())
    diffs = compare_metadata(context, Base.metadata)

    def mentions_index(diff) -> bool:
        items = diff if isinstance(diff, list) else [diff]
        return any(
            getattr(item[1], "name", None) in INDEXES
            for item in items
            if isinstance(item, tuple) and len(item) > 1
        )

    assert [diff for diff in diffs if mentions_index(diff)] == []


def test_gateway_spend_filter_can_use_the_partial_index(db_session):
    """The partial predicate must stay provably implied by the CRUD filter.

    ``ix_api_usage_gateway_spend`` is only usable while its WHERE clause
    matches ``exclude_replay_usage_condition`` exactly; if either side drifts
    the planner silently falls back to reading every gateway row.
    """
    import uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from preloop.models.crud.api_usage import exclude_replay_usage_condition
    from preloop.models.models import ApiUsage

    query = (
        select(ApiUsage.account_id, func.sum(ApiUsage.estimated_cost))
        .where(
            ApiUsage.account_id.in_([uuid.uuid4()]),
            ApiUsage.action_type == "model_gateway",
            ApiUsage.timestamp >= datetime.now(timezone.utc) - timedelta(days=30),
            exclude_replay_usage_condition(),
        )
        .group_by(ApiUsage.account_id)
    )
    compiled = query.compile(
        dialect=db_session.get_bind().dialect,
        compile_kwargs={"literal_binds": True},
    )
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    db_session.execute(text("SET LOCAL enable_bitmapscan = off"))
    plan = "\n".join(row[0] for row in db_session.execute(text(f"EXPLAIN {compiled}")))
    assert "ix_api_usage_gateway_spend" in plan, plan
