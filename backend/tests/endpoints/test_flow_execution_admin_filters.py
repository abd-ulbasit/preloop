"""Global monitoring counts failures when they end, not when they start."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_flow_execution
from preloop.models.schemas.flow_execution import (
    FlowExecutionCreate,
    FlowExecutionUpdate,
)

from tests.endpoints.test_flow_execution_list_filters import _create_flow


def test_end_time_window_matches_page_and_count(
    db_session: Session, test_user: models.User
) -> None:
    flow = _create_flow(db_session, test_user, "Long-running failure")
    now = datetime.now(timezone.utc)
    recent = crud_flow_execution.create(
        db_session, FlowExecutionCreate(flow_id=flow.id, status="FAILED")
    )
    old = crud_flow_execution.create(
        db_session, FlowExecutionCreate(flow_id=flow.id, status="FAILED")
    )
    for run, ended in [(recent, now), (old, now - timedelta(days=2))]:
        crud_flow_execution.update(
            db_session,
            run,
            FlowExecutionUpdate(start_time=now - timedelta(days=3), end_time=ended),
        )
    filters = {
        "account_id": str(test_user.account_id),
        "statuses": ["FAILED"],
        "ended_after": now - timedelta(minutes=1),
    }
    rows = crud_flow_execution.get_multi(db_session, **filters)
    assert [row.id for row in rows] == [recent.id]
    assert crud_flow_execution.count(db_session, **filters) == 1


def test_lightweight_projection_and_equal_time_pagination(
    db_session: Session, test_user: models.User
) -> None:
    flow = _create_flow(db_session, test_user, "Equal start times")
    now = datetime.now(timezone.utc)
    ids = []
    for _ in range(2):
        run = crud_flow_execution.create(
            db_session, FlowExecutionCreate(flow_id=flow.id, status="RUNNING")
        )
        ids.append(run.id)
        crud_flow_execution.update(db_session, run, FlowExecutionUpdate(start_time=now))
    db_session.expire_all()
    statements: list[str] = []

    def record(*args: object) -> None:
        statements.append(str(args[2]))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        rows = [
            crud_flow_execution.get_multi(
                db_session,
                account_id=str(test_user.account_id),
                limit=1,
                skip=skip,
                lightweight=True,
                eager_load=True,
            )[0]
            for skip in [0, 1]
        ]
        assert [run.id for run in rows] == sorted(ids, reverse=True)
        before = len(statements)
        assert all(run.flow.account_id == test_user.account_id for run in rows)
        assert len(statements) == before
    finally:
        event.remove(bind, "before_cursor_execute", record)
