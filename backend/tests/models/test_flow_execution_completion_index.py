"""Completion monitoring can page failures without sorting the whole table."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_flow_execution


def test_completion_index_remains_in_model_metadata(db_session: Session) -> None:
    """Autogenerate must not drop the index after a model declaration is lost."""
    name = "ix_flow_execution_status_completion"
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


def test_failure_completion_page_uses_index_without_sort(db_session: Session) -> None:
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
            statuses=["FAILED"],
            sort_by="end_time",
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
    assert "ix_flow_execution_status_completion" in plan
    assert "Sort" not in plan
