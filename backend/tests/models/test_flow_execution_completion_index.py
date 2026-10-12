"""Completion monitoring can page failures without sorting the whole table."""

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from preloop.models.crud import crud_flow_execution


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
