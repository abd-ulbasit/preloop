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

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from preloop.models.crud import crud_api_usage

# The values a caller varies: these stay parameters in the generic plan.
VARIABLE = ("account_id", "timestamp", "ai_model_id")
COVERING = ("ix_api_usage_gateway_totals", "ix_api_usage_gateway_model_totals")
ACCOUNT = "00000000-0000-0000-0000-0000000000a1"
MODELS = [f"00000000-0000-0000-0000-0000000000b{n}" for n in range(3)]


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

    Named ``%(name)s`` placeholders become ``$n`` so PostgreSQL plans it the
    way it plans a prepared statement, whichever driver runs the tests. Only
    the covering indexes and a sequential scan stay available, so the plan
    shows whether their predicate can be matched at all. The test
    transaction rolls the catalogue change back.
    """
    numbers: dict[str, int] = {}

    def _placeholder(match: re.Match) -> str:
        name = match.group(1)
        if not name.startswith(VARIABLE):
            # Constants such as the 0 of a COALESCE: a generic plan cannot
            # type an untyped parameter inside SUM, and they decide nothing.
            value = parameters[name]
            assert isinstance(value, (int, float)), (name, value)
            return repr(value)
        number = numbers.setdefault(name, len(numbers) + 1)
        return f"${number}"

    generic = re.sub(r"%\(([^)]+)\)s", _placeholder, statement)
    assert any(name.startswith("timestamp") for name in numbers), numbers
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    db_session.execute(
        text(
            """
            UPDATE pg_index AS i SET indisvalid = false
            FROM pg_class AS c
            WHERE c.oid = i.indexrelid
              AND i.indrelid = 'api_usage'::regclass
              AND NOT (c.relname = ANY(:keep))
            """
        ),
        {"keep": list(COVERING)},
    )
    rows = db_session.connection().exec_driver_sql("EXPLAIN (GENERIC_PLAN) " + generic)
    return "\n".join(row[0] for row in rows)


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
    assert any(f"Index Only Scan using {index}" in plan for index in COVERING), plan


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


def test_model_keyed_covering_index_is_built_valid(db_session):
    """The model-keyed covering index exists, is valid, and has the predicate."""
    row = db_session.execute(
        text(
            """
            SELECT i.indisvalid, pg_get_indexdef(i.indexrelid)
            FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            WHERE c.relname = 'ix_api_usage_gateway_model_totals'
            """
        )
    ).one()
    valid, definition = row
    assert valid is True
    assert '(account_id, ai_model_id, "timestamp")' in definition
    for column in ("estimated_cost", "cache_creation_tokens", "status_code"):
        assert column in definition, definition
    assert "replay_validation" in definition
