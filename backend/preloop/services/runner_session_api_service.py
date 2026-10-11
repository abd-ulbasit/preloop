"""Runner session service for the HTTP API (#1483 seam, #1482 backend).

The routes in ``preloop.api.endpoints.runner_sessions`` authorize, validate,
mint the clone credential and audit. This adapter persists through
``preloop.services.runner_sessions`` and delivers frames to the runner. The
routes own the audit trail, so the service functions are called with
``audit=False``.

The clone credential (#1484) only ever travels in the ``session_start`` frame
sent here over the runner socket held by this process. It is never stored,
so a start that is delivered later by a heartbeat (another replica holds the
socket) carries no credential and the runner refuses the checkout with
``checkout_failed``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_flow_runner, crud_runner_remote_session
from preloop.services import runner_sessions as sessions

logger = logging.getLogger(__name__)


def _error(code: str, message: str) -> Exception:
    from preloop.api.endpoints.runner_sessions import RunnerSessionError

    return RunnerSessionError(code, message)


def to_record(row: models.RunnerRemoteSession) -> Any:
    """The endpoint's view of one stored session."""
    from preloop.api.endpoints.runner_sessions import RemoteSessionRecord

    workspace = dict(row.workspace or {})
    workspace.setdefault("label", row.workspace_label)
    return RemoteSessionRecord(
        session_id=str(row.runtime_session_id or row.id),
        remote_session_id=str(row.id),
        runner_id=str(row.runner_id),
        account_id=str(row.account_id),
        actor_user_id=str(row.actor_user_id) if row.actor_user_id else None,
        harness=row.harness,
        model=row.model,
        state=row.state,
        workspace=workspace,
        turn_in_progress=bool(
            row.pending_turn or row.active_turn_id or row.first_prompt
        ),
        started_at=row.started_at or row.requested_at,
        last_activity_at=row.last_activity_at,
        end_reason=row.end_reason,
    )


def _runner_socket(runner: Any) -> Any:
    from preloop.api.endpoints.runners import _live

    return _live.get(str(runner.id))


class RunnerRemoteSessionService:
    """Implements ``RunnerSessionService`` on ``runner_remote_sessions``."""

    def count_active(self, db: Session, *, runner_id: str) -> int:
        return len(
            crud_runner_remote_session.list_live_for_runner(db, runner_id=runner_id)
        )

    def list_for_runner(self, db: Session, *, runner_id: str, limit: int) -> List[Any]:
        rows = crud_runner_remote_session.list_for_runner(
            db, runner_id=runner_id, limit=limit
        )
        return [to_record(row) for row in rows]

    def get(self, db: Session, *, session_id: str) -> Optional[Any]:
        row = crud_runner_remote_session.get_by_reference(db, session_ref=session_id)
        return to_record(row) if row is not None else None

    def _row(self, db: Session, record: Any) -> models.RunnerRemoteSession:
        row = crud_runner_remote_session.get_for_account(
            db,
            account_id=record.account_id,
            remote_session_id=record.remote_session_id,
        )
        if row is None:
            raise _error("session_ended", "The session no longer exists.")
        return row

    async def start(self, db: Session, plan: Any) -> Any:
        try:
            row = sessions.request_session(
                db,
                runner=plan.runner,
                actor=plan.actor,
                harness=plan.harness,
                model=plan.model,
                workspace=plan.workspace,
                workspace_label=plan.workspace.get("label"),
                first_prompt=plan.first_prompt,
                title=plan.title,
                idle_timeout_seconds=(plan.limits or {}).get("idle_timeout_seconds"),
                session_id=plan.remote_session_id,
                audit=False,
            )
        except sessions.RunnerSessionError as exc:
            raise _error(exc.code, str(exc)) from None
        ws = _runner_socket(plan.runner)
        if ws is not None:
            frame = sessions.build_start_message(db, row, credential=plan.credential)
            try:
                await ws.send_json(frame)
                row.start_sent_at = datetime.now(timezone.utc)
                db.add(row)
                db.commit()
            except Exception:  # noqa: BLE001 - the heartbeat path redelivers
                logger.info(
                    "Immediate session_start to runner %s failed", plan.runner.id
                )
        return to_record(row)

    async def send_turn(
        self, db: Session, record: Any, *, turn_id: str, text: str
    ) -> None:
        row = self._row(db, record)
        try:
            sessions.queue_turn(
                db, row, actor=None, text=text, turn_id=turn_id, audit=False
            )
        except sessions.RunnerSessionError as exc:
            raise _error(exc.code, str(exc)) from None
        await self._push(db, row)

    async def stop(self, db: Session, record: Any, *, mode: str) -> Any:
        row = self._row(db, record)
        sessions.request_stop(db, row, actor_user_id=None, mode=mode, audit=False)
        await self._push(db, row)
        db.refresh(row)
        return to_record(row)

    async def _push(self, db: Session, row: models.RunnerRemoteSession) -> None:
        runner = crud_flow_runner.get(db, id=row.runner_id)
        if runner is not None:
            await sessions.push_pending_to_runner(db, runner)
