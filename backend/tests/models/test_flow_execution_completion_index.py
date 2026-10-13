"""Completion monitoring can page failures without sorting the whole table."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_flow_execution


def _completion_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "preloop/models/alembic/versions"
        / "20261014_flow_execution_completion_index.py"
    )
    spec = importlib.util.spec_from_file_location("completion_index_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade_completion_index(module: ModuleType, connection: Connection) -> None:
    connection.commit()
    context = MigrationContext.configure(connection)
    with context.begin_transaction(), Operations.context(context):
        module.upgrade()


@pytest.mark.parametrize(
    "name", ["ix_flow_execution_status_completion", "ix_flow_execution_start_time_id"]
)
def test_migration_repairs_invalid_interrupted_index(
    db_engine: Engine, name: str
) -> None:
    """A retried concurrent build must rebuild rather than skip an invalid index."""
    migration = _completion_migration()
    with db_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        valid_query = text(
            "SELECT indisvalid FROM pg_index WHERE indexrelid = CAST(:name AS regclass)"
        )
        assert connection.execute(valid_query, {"name": name}).scalar() is True
        connection.execute(
            text(
                "UPDATE pg_index SET indisvalid = false "
                "WHERE indexrelid = CAST(:name AS regclass)"
            ),
            {"name": name},
        )
        try:
            assert connection.execute(valid_query, {"name": name}).scalar() is False
            _upgrade_completion_index(migration, connection)
            assert connection.execute(valid_query, {"name": name}).scalar() is True
            # The normal retry must also be harmless when the index is valid.
            _upgrade_completion_index(migration, connection)
            assert connection.execute(valid_query, {"name": name}).scalar() is True
        finally:
            # Preserve the shared test schema even if an assertion fails.
            _upgrade_completion_index(migration, connection)


@pytest.mark.parametrize(
    "name", ["ix_flow_execution_status_completion", "ix_flow_execution_start_time_id"]
)
def test_completion_index_remains_in_model_metadata(
    db_session: Session, name: str
) -> None:
    """Autogenerate must not drop the index after a model declaration is lost."""
    assert name in {index.name for index in models.FlowExecution.__table__.indexes}
    context = MigrationContext.configure(db_session.connection())
    changes: dict[str, set[str]] = {"add_index": set(), "remove_index": set()}
    for diff in compare_metadata(context, models.FlowExecution.metadata):
        for item in diff if isinstance(diff, list) else [diff]:
            if isinstance(item, tuple) and item[0] in changes:
                changes[item[0]].add(item[1].name)
    # DESC expressions can appear as matching remove/add operations; an
    # unmatched removal means the production index would be dropped.
    assert name not in changes["remove_index"] - changes["add_index"]


@pytest.mark.parametrize(
    ("sort_by", "index"),
    [
        ("end_time", "ix_flow_execution_status_completion"),
        ("start_time", "ix_flow_execution_start_time_id"),
    ],
)
def test_execution_page_uses_index_without_sort(
    db_session: Session, sort_by: str, index: str
) -> None:
    statements: list[tuple[str, object]] = []

    def capture(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: object,
    ) -> None:
        statements.append((statement, parameters))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        crud_flow_execution.get_multi(
            db_session,
            statuses=["FAILED"] if sort_by == "end_time" else None,
            sort_by=sort_by,
            limit=20,
            lightweight=True,
            eager_load=False,
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    statement, parameters = statements[0]
    # Tiny test tables normally favor a sequential scan. Disable it locally
    # to verify index eligibility and ordering, rather than planner cost guesses.
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        row[0]
        for row in db_session.connection().exec_driver_sql(
            "EXPLAIN " + statement, parameters
        )
    )
    assert index in plan
    assert "Sort" not in plan
