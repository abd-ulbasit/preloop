"""Model detail lists stay on an index page, however many requests the model has.

Issue #1688: ``GET /ai-models/{id}/interactions`` and
``GET /ai-models/{id}/runtime-sessions`` walked every gateway row of a busy
model (and the interaction count sequentially scanned the search corpus).
A model with 50,000 usage rows must still be answered from
``ix_api_usage_account_model_ts`` with a LIMIT, and must not scan
``api_usage`` or ``gateway_usage_search_document`` from one end to the other.
The assertion is the plan, not the wall clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from preloop.models.crud import (
    crud_gateway_usage_search_document,
    crud_runtime_session,
)
from preloop.services.model_gateway_usage import model_detail_window

ROWS = 50_000
# Rows for a second model, in the same window. Without them every row the
# timestamp index returns belongs to the model under test, and the planner
# never has a reason to pick ``ix_api_usage_account_model_ts``.
OTHER_ROWS = 200_000
SESSIONS = 40
INDEX = "ix_api_usage_account_model_ts"


def test_omitted_model_detail_window_is_seven_days() -> None:
    """Callers that name no dates get 7 days, not the 30-day usage default."""
    start, end = model_detail_window(None, None)
    assert start is not None and end is not None
    assert abs((end - start) - timedelta(days=7)) < timedelta(seconds=2)
    explicit = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert model_detail_window(explicit, explicit) == (explicit, explicit)


def _committed_seed(engine) -> dict:
    """Insert the busy model outside the test transaction and analyze it.

    ``ANALYZE`` only sees committed rows. A seed that stays inside the test's
    rollback looks empty to the planner, which then picks a one-row index
    and never considers ``ix_api_usage_account_model_ts``.
    """
    side = Session(bind=engine)
    account_id = side.execute(text("SELECT gen_random_uuid()")).scalar()
    model_id = side.execute(text("SELECT gen_random_uuid()")).scalar()
    try:
        side.execute(
            text(
                """
                INSERT INTO account (
                    id, root_account_id, hierarchy_path, hierarchy_depth,
                    organization_name, is_active, email_verified, is_superuser
                )
                VALUES (
                    :id, :id, ARRAY[:id]::uuid[], 0,
                    'example.com busy model', true, false, false
                )
                """
            ),
            {"id": account_id},
        )
        side.execute(
            text(
                """
                INSERT INTO ai_model (
                    id, name, provider_name, model_identifier, account_id,
                    is_default, created_at, updated_at
                )
                VALUES (
                    :id, 'Busy', 'example', 'busy-model', :account,
                    false, now(), now()
                )
                """
            ),
            {"id": model_id, "account": account_id},
        )
        side.commit()
        _seed(side, account_id, model_id)
        side.commit()
    except Exception:
        side.rollback()
        _delete_seed(side, account_id)
        side.commit()
        side.close()
        raise
    side.close()
    # ANALYZE inside a transaction does not set the visibility map, so the
    # planner will not pick an index-only scan of the model index. VACUUM
    # cannot run inside a transaction block.
    autocommit = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        autocommit.execute(text("VACUUM ANALYZE api_usage"))
        autocommit.execute(text("VACUUM ANALYZE gateway_usage_search_document"))
        autocommit.execute(text("VACUUM ANALYZE runtime_session"))
    except Exception:
        cleanup = Session(bind=engine)
        try:
            _delete_seed(cleanup, account_id)
            cleanup.commit()
        finally:
            cleanup.close()
        raise
    finally:
        autocommit.close()
    return {"account_id": account_id, "model_id": model_id, "engine": engine}


def _delete_seed(side: Session, account_id) -> None:
    side.execute(
        text(
            """
            DELETE FROM gateway_usage_search_document d
            USING api_usage u
            WHERE d.api_usage_id = u.id AND u.account_id = :id
            """
        ),
        {"id": account_id},
    )
    side.execute(
        text("DELETE FROM api_usage WHERE account_id = :id"), {"id": account_id}
    )
    side.execute(
        text("DELETE FROM runtime_session WHERE account_id = :id"),
        {"id": account_id},
    )
    side.execute(
        text("DELETE FROM ai_model WHERE account_id = :id"), {"id": account_id}
    )
    side.execute(text("DELETE FROM account WHERE id = :id"), {"id": account_id})


def _seed(db_session, account_id, model_id) -> None:
    db_session.execute(
        text(
            """
            CREATE TEMP TABLE model_detail_sessions (n int PRIMARY KEY, id uuid)
            ON COMMIT DROP
            """
        )
    )
    db_session.execute(
        text(
            """
            INSERT INTO model_detail_sessions (n, id)
            SELECT g, gen_random_uuid() FROM generate_series(1, :n) AS g
            """
        ),
        {"n": SESSIONS},
    )
    db_session.execute(
        text(
            """
            INSERT INTO runtime_session (
                id, account_id, session_source_type, session_source_id,
                started_at, created_at, updated_at
            )
            SELECT id, :account, 'custom', 'busy-' || n, now(), now(), now()
            FROM model_detail_sessions
            """
        ),
        {"account": account_id},
    )
    db_session.execute(
        text(
            """
            INSERT INTO api_usage (
                id, account_id, ai_model_id, runtime_session_id, endpoint,
                method, status_code, duration, action_type, timestamp,
                prompt_tokens, completion_tokens, total_tokens,
                estimated_cost, provider_name, model_alias,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                :account,
                :model,
                s.id,
                '/openai/v1/chat/completions',
                'POST',
                200,
                0.1,
                'model_gateway',
                CASE
                    WHEN g <= :rows THEN now() - (g % 30) * interval '1 day'
                    ELSE now() - interval '200 days'
                END,
                10, 1, 11, 0.0001, 'example', 'busy', now(), now()
            FROM generate_series(1, :rows * 2) AS g
            JOIN model_detail_sessions s ON s.n = (g % :sessions) + 1
            """
        ),
        {
            "account": account_id,
            "model": model_id,
            "rows": ROWS,
            "sessions": SESSIONS,
        },
    )
    other_model = db_session.execute(text("SELECT gen_random_uuid()")).scalar()
    db_session.execute(
        text(
            """
            INSERT INTO ai_model (
                id, name, provider_name, model_identifier, account_id,
                is_default, created_at, updated_at
            )
            VALUES (
                :id, 'Other', 'example', 'other-model', :account,
                false, now(), now()
            )
            """
        ),
        {"id": other_model, "account": account_id},
    )
    db_session.execute(
        text(
            """
            INSERT INTO api_usage (
                id, account_id, ai_model_id, endpoint, method, status_code,
                duration, action_type, timestamp, prompt_tokens,
                completion_tokens, total_tokens, estimated_cost,
                provider_name, model_alias, created_at, updated_at
            )
            SELECT
                gen_random_uuid(), :account, :model,
                '/openai/v1/chat/completions', 'POST', 200, 0.1,
                'model_gateway',
                now() - (g % 30) * interval '1 day',
                1, 1, 2, 0, 'example', 'other', now(), now()
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"account": account_id, "model": other_model, "rows": OTHER_ROWS},
    )
    db_session.execute(
        text(
            """
            INSERT INTO gateway_usage_search_document (
                id, api_usage_id, content_hash, searchable_text,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(), u.id, md5(u.id::text), 'captured prompt',
                now(), now()
            FROM api_usage u
            WHERE u.account_id = :account
              AND u.ai_model_id = :model
              AND u.timestamp > now() - interval '40 days'
            """
        ),
        {"account": account_id, "model": model_id},
    )
    db_session.flush()


def _capture(db_session, call):
    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _record)
    try:
        result = call()
    finally:
        event.remove(bind, "before_cursor_execute", _record)
    return result, statements


def _plans(db_session, statements):
    connection = db_session.connection()
    raw = connection.connection.driver_connection
    plans = []
    with raw.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        for statement, parameters in statements:
            cursor.execute("EXPLAIN " + statement, parameters)
            plans.append(
                (
                    " ".join(statement.split()),
                    "\n".join(row[0] for row in cursor.fetchall()),
                )
            )
    return plans


def _assert_bounded(plans) -> None:
    """No full scan, and the model/time index serves the page."""
    saw_index = False
    saw_limit = False
    for statement, plan in plans:
        assert "Seq Scan on api_usage" not in plan, plan
        assert "Seq Scan on gateway_usage_search_document" not in plan, plan
        # A leading count is the exact total. Per-session aggregates also
        # contain count(), and those are limited to the page.
        if statement.lower().startswith("select count("):
            raise AssertionError(f"exact count still runs:\n{statement[:400]}")
        if INDEX in plan and "Limit" in plan:
            saw_index = True
            saw_limit = True
        if " LIMIT " in f" {statement.upper()} ":
            saw_limit = True
    rendered = "\n\n".join(f"{sql[:300]}\n{plan}" for sql, plan in plans)
    assert saw_index, "page probe did not use " + INDEX + "\n" + rendered
    assert saw_limit, "page probe has no LIMIT\n" + rendered


def test_busy_model_lists_use_the_model_time_index(db_session):
    """50k rows for one model: both lists are an index page, not a full scan."""
    bind = db_session.get_bind()
    engine = getattr(bind, "engine", bind)
    seeded = _committed_seed(engine)
    account_id = str(seeded["account_id"])
    model_id = str(seeded["model_id"])
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    try:
        _assert_busy_model_lists(db_session, account_id, model_id, start, end)
    finally:
        side = Session(bind=engine)
        _delete_seed(side, seeded["account_id"])
        side.commit()
        side.close()


def _assert_busy_model_lists(db_session, account_id, model_id, start, end) -> None:
    interactions, interaction_sql = _capture(
        db_session,
        lambda: crud_gateway_usage_search_document.list_model_interaction_page(
            db_session,
            account_id=account_id,
            ai_model_id=model_id,
            start_date=start,
            end_date=end,
            limit=10,
            offset=0,
        ),
    )
    assert len(interactions["items"]) == 10
    assert interactions["has_more"] is True
    assert interactions["total"] is None
    _assert_bounded(_plans(db_session, interaction_sql))

    sessions, session_sql = _capture(
        db_session,
        lambda: crud_runtime_session.list_account_sessions(
            db_session,
            account_id=account_id,
            start_date=start,
            end_date=end,
            ai_model_id=model_id,
            limit=10,
            offset=0,
        ),
    )
    assert len(sessions["items"]) == 10
    assert sessions["has_more"] is True
    assert sessions["total"] is None
    _assert_bounded(_plans(db_session, session_sql))
