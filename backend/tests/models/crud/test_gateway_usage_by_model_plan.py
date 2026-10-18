"""The per-model gateway aggregate can use its covering index in any plan.

``GET /ai-models/overview`` aggregates a 30-day window per model through
``get_gateway_usage_by_model``. ``ix_api_usage_gateway_totals`` covers every
column it reads, but PostgreSQL only uses a partial index whose predicate it
can prove from the query's constants. psycopg prepares a statement after a few
executions, and a generic prepared plan sees bound parameters, not constants:
with ``action_type = $1`` and ``meta_data ->> $2 <> $3`` the covering index is
unusable and every request reads the heap. The assertion is the plan.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg._queries import PostgresQuery
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from preloop.models.crud import crud_api_usage

INDEX = "ix_api_usage_gateway_totals"
ACCOUNT = "00000000-0000-0000-0000-0000000000a1"
MODELS = [f"00000000-0000-0000-0000-0000000000b{n}" for n in range(3)]
MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "preloop"
    / "models"
    / "alembic"
    / "versions"
    / "20261030_api_usage_autovacuum.py"
)


def _captured_select(db_session: Session, **kwargs) -> tuple[str, dict]:
    statements: list[tuple[str, dict]] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    bind = db_session.get_bind()
    end = datetime.now(timezone.utc)
    event.listen(bind, "before_cursor_execute", _record)
    try:
        crud_api_usage.get_gateway_usage_by_model(
            db_session,
            account_id=ACCOUNT,
            start_date=end - timedelta(days=30),
            end_date=end,
            limit=None,
            **kwargs,
        )
    finally:
        event.remove(bind, "before_cursor_execute", _record)
    assert statements, "aggregate was not issued"
    return statements[0]


def _generic_plan(db_session: Session, statement: str, parameters: dict) -> str:
    """EXPLAIN the statement as a generic prepared plan, other indexes hidden.

    Only the covering index and a sequential scan stay available, so the plan
    shows whether the index predicate can be matched at all. The test
    transaction rolls the catalogue change back.
    """
    raw = db_session.connection().connection.driver_connection
    query = PostgresQuery(psycopg.adapt.Transformer(raw))
    query.convert("EXPLAIN (GENERIC_PLAN) " + statement, parameters)
    with raw.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            """
            UPDATE pg_index AS i SET indisvalid = false
            FROM pg_class AS c
            WHERE c.oid = i.indexrelid
              AND i.indrelid = 'api_usage'::regclass
              AND c.relname <> %s
            """,
            (INDEX,),
        )
        cursor.execute(query.query.decode())
        return "\n".join(row[0] for row in cursor.fetchall())


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"ai_model_ids": MODELS},
        {
            "ai_model_ids": MODELS,
            "failed_since": {MODELS[0]: datetime(2026, 1, 1, tzinfo=timezone.utc)},
        },
    ],
    ids=["account", "model_ids", "failed_since"],
)
def test_generic_plan_is_index_only_on_the_covering_index(db_session, kwargs):
    """Prepared statements keep the aggregate off the heap."""
    statement, parameters = _captured_select(db_session, **kwargs)
    plan = _generic_plan(db_session, statement, parameters)
    assert f"Index Only Scan using {INDEX}" in plan, plan


def test_index_predicate_is_spelled_with_literals(db_session):
    """The partial-index predicate reaches PostgreSQL as constants."""
    statement, parameters = _captured_select(db_session, ai_model_ids=MODELS)
    assert "api_usage.action_type = 'model_gateway'" in statement
    assert "(api_usage.meta_data ->> 'purpose') IS NULL" in statement
    assert "!= 'replay_validation'" in statement
    assert not any(
        value in ("model_gateway", "purpose", "replay_validation")
        for value in parameters.values()
    )


def test_migration_tunes_api_usage_autovacuum(db_session):
    """Index-only scans need a current visibility map on the hot table."""
    spec = importlib.util.spec_from_file_location("api_usage_autovac", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = db_session.execute(
        text("SELECT reloptions FROM pg_class WHERE relname = 'api_usage'")
    ).scalar()
    for key, value in module.AUTOVACUUM.items():
        assert f"{key}={value}" in (options or []), options
