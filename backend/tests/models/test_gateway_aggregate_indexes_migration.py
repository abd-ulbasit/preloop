"""The gateway aggregate index migration (#1582, #1583).

Pins that the model metadata declares the three indexes (else autogenerate
proposes dropping them) and that the reads they exist for can use them.
"""

from datetime import datetime, timedelta, timezone

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from preloop.models.models.base import Base

INDEXES = (
    "ix_api_usage_gateway_totals",
    "ix_api_usage_gateway_subscription",
    "ix_api_usage_rate_limit_snapshot",
)


def test_model_metadata_declares_the_indexes(db_session):
    """Autogenerate must not see a migrated index as one to drop.

    Alembic cannot compare DESC or expression columns, so such indexes show
    up as a remove + add pair of the same name (as every DESC index in this
    schema does). A remove without the matching add means the model lost it.
    """
    context = MigrationContext.configure(db_session.connection())
    diffs = compare_metadata(context, Base.metadata)
    ops: dict[str, set[str]] = {"add_index": set(), "remove_index": set()}
    for diff in diffs:
        for item in diff if isinstance(diff, list) else [diff]:
            if isinstance(item, tuple) and item[0] in ops:
                ops[item[0]].add(getattr(item[1], "name", None))
    for name in INDEXES:
        assert name not in ops["remove_index"] - ops["add_index"], name


def test_indexes_exist_and_are_valid(db_session):
    rows = db_session.execute(
        text(
            "SELECT c.relname, i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = ANY(:names)"
        ),
        {"names": list(INDEXES)},
    ).all()
    assert sorted(rows) == sorted((name, True) for name in INDEXES)


def _plan(db_session, statement, parameters) -> str:
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    db_session.execute(text("SET LOCAL enable_bitmapscan = off"))
    return "\n".join(
        row[0]
        for row in db_session.connection().exec_driver_sql(
            "EXPLAIN " + statement, parameters
        )
    )


def _captured(db_session, call):
    from sqlalchemy import event

    statements = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _capture)
    try:
        call()
    finally:
        event.remove(bind, "before_cursor_execute", _capture)
    return statements


def test_gateway_totals_read_only_covered_columns(db_session, test_user):
    """The window totals read only columns the covering index carries.

    An index-only scan needs every referenced column in the index. The plan
    itself also depends on the visibility map, which a rolled-back test
    transaction cannot set, so the coverage is pinned structurally: key and
    predicate columns plus GATEWAY_TOTALS_INCLUDE.
    """
    import re

    from preloop.models.crud import crud_api_usage
    from preloop.models.models.api_usage import GATEWAY_TOTALS_INCLUDE

    end = datetime.now(timezone.utc)
    statements = _captured(
        db_session,
        lambda: crud_api_usage.get_gateway_usage_summary(
            db_session,
            account_id=str(test_user.account_id),
            start_date=end - timedelta(days=30),
            end_date=end,
        ),
    )
    statement, _ = statements[-1]
    referenced = set(re.findall(r"\bapi_usage\.(\w+)", statement))
    key_and_predicate = {"account_id", "timestamp", "action_type", "meta_data"}
    assert referenced - key_and_predicate <= set(GATEWAY_TOTALS_INCLUDE), (
        referenced - key_and_predicate - set(GATEWAY_TOTALS_INCLUDE)
    )


def test_subscription_block_uses_its_partial_index(db_session, test_user):
    """The Spend subscription block no longer scans the whole table."""
    from preloop.models.crud import crud_api_usage

    end = datetime.now(timezone.utc)
    statements = _captured(
        db_session,
        lambda: crud_api_usage.get_subscription_usage_summary(
            db_session,
            account_id=str(test_user.account_id),
            start=end - timedelta(days=30),
            end=end,
        ),
    )
    statement, parameters = statements[-1]
    plan = _plan(db_session, statement, parameters)
    assert "ix_api_usage_gateway_subscription" in plan, plan
