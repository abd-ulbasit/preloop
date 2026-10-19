"""The agents list reads per-principal usage totals from a covering index.

``GET /agents`` sums each listed principal's gateway usage and reads its
latest request. With only ``ix_api_usage_account_principal_ts`` every usage
row was a heap fetch: 175k buffers and 2.2 s on a cold prod cache (#1694).
``ix_api_usage_principal_totals`` carries the summed columns, so both LATERAL
reads must plan as index-only scans. The assertion is the plan.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from preloop.models.crud.managed_agent import _usage_aggregates_for_principals

INDEX = "ix_api_usage_principal_totals"
ACCOUNT = "00000000-0000-0000-0000-0000000000a1"
PRINCIPALS = [("claude_code", "claude-code-1"), ("codex", "codex-1")]


def _captured_usage_select(db_session: Session) -> tuple[str, dict]:
    statements: list[tuple[str, dict]] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if "principal_usage_totals" in statement:
            statements.append((statement, parameters))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _record)
    try:
        _usage_aggregates_for_principals(
            db_session,
            account_id=ACCOUNT,
            principals=PRINCIPALS,
            start_date=datetime(2026, 1, 1, tzinfo=UTC),
        )
    finally:
        event.remove(bind, "before_cursor_execute", _record)
    assert len(statements) == 1, statements
    return statements[0]


def _literal(value: object) -> str:
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def test_gateway_predicate_is_a_literal(db_session: Session) -> None:
    """Prepared statements can match the partial index predicate."""
    statement, parameters = _captured_usage_select(db_session)
    assert "api_usage.action_type = 'model_gateway'" in statement
    assert "model_gateway" not in parameters.values()


def test_principal_totals_plan_is_index_only(db_session: Session) -> None:
    """Totals and latest-request LATERALs both stay inside the covering index."""
    statement, parameters = _captured_usage_select(db_session)
    inlined = re.sub(
        r"%\(([^)]+)\)s", lambda m: _literal(parameters[m.group(1)]), statement
    )
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    db_session.execute(text("SET LOCAL enable_bitmapscan = off"))
    db_session.execute(
        text(
            """
            UPDATE pg_index AS i SET indisvalid = false
            FROM pg_class AS c
            WHERE c.oid = i.indexrelid
              AND i.indrelid = 'api_usage'::regclass
              AND c.relname NOT IN (:keep, 'ix_api_usage_account_principal_ts')
            """
        ),
        {"keep": INDEX},
    )
    rows = db_session.connection().exec_driver_sql("EXPLAIN " + inlined)
    plan = "\n".join(row[0] for row in rows)
    assert plan.count(f"Index Only Scan using {INDEX}") == 2, plan
    assert "Index Scan using ix_api_usage_account_principal_ts" not in plan, plan
