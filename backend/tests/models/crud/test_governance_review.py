"""CRUD rules for the governance review tables."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud.governance_review import (
    crud_governance_finding,
    crud_governance_policy_document,
    crud_governance_review_run,
    crud_governance_review_settings,
    crud_governance_session_digest,
)
from preloop.models.models.governance_review import (
    DIGEST_MAX_BYTES,
    GovernancePolicyDocument,
    GovernanceReviewSettings,
)
from preloop.models.models.runtime_session import RuntimeSession
from preloop.models.models.user import User


def _window() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc),
    )


def _session(db_session: Session, account_id: UUID) -> RuntimeSession:
    row = models.RuntimeSession(
        account_id=account_id,
        session_source_type="cli",
        session_source_id=str(uuid4()),
        started_at=datetime(2026, 10, 13, 10, 0),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _finding(run_id: UUID, fingerprint: str = "unused-tools") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "kind": "inefficiency",
        "category": "unused_tools",
        "severity": "low",
        "title": "Unused tool",
        "suggestion": "Detach the tool.",
        "fingerprint": fingerprint,
        "evidence": [{"excerpt": "attached, never called", "source": "metadata"}],
        "rule_ref": "prefer.tools",
    }


def test_two_versions_leave_one_active_row(
    db_session: Session, test_user: User
) -> None:
    first = crud_governance_policy_document.create_version(
        db_session,
        account_id=test_user.account_id,
        body_markdown="First",
        source="default_template",
        commit=False,
    )
    second = crud_governance_policy_document.create_version(
        db_session,
        account_id=test_user.account_id,
        body_markdown="Second",
        source="user",
        created_by_id=test_user.id,
        commit=False,
    )

    active = crud_governance_policy_document.get_active(
        db_session, account_id=test_user.account_id
    )
    history = crud_governance_policy_document.list_history(
        db_session, account_id=test_user.account_id
    )

    assert first.version == 1
    assert second.version == 2
    assert active is not None
    assert active.id == second.id
    assert [row.version for row in history] == [2, 1]
    assert history[1].is_active is False


def test_partial_unique_index_rejects_a_second_active_row(
    db_session: Session, test_user: User
) -> None:
    crud_governance_policy_document.create_version(
        db_session,
        account_id=test_user.account_id,
        body_markdown="Active",
        source="user",
        commit=False,
    )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "INSERT INTO governance_policy_document "
                    "(id, created_at, updated_at, account_id, version, "
                    "body_markdown, is_active, source) "
                    "VALUES (:id, now(), now(), :account_id, 2, 'other', "
                    "true, 'user')"
                ),
                {"id": uuid4(), "account_id": test_user.account_id},
            )


def test_concurrent_new_versions_leave_one_active_row(db_engine: Engine) -> None:
    """Two callers serialise on the advisory lock and the partial unique index."""
    account_id = uuid4()
    with Session(db_engine) as db:
        db.add(models.Account(id=account_id, organization_name="Governance race"))
        db.commit()
    barrier = Barrier(2)

    def create() -> int:
        barrier.wait(timeout=5)
        with Session(db_engine) as db:
            row = crud_governance_policy_document.create_version(
                db,
                account_id=account_id,
                body_markdown="Concurrent policy",
                source="user",
            )
            return row.version

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            versions = sorted(pool.map(lambda _: create(), range(2)))
        assert versions == [1, 2]
        with Session(db_engine) as db:
            active = (
                db.query(GovernancePolicyDocument)
                .filter(
                    GovernancePolicyDocument.account_id == account_id,
                    GovernancePolicyDocument.is_active.is_(True),
                )
                .all()
            )
            assert len(active) == 1
            assert active[0].version == 2
    finally:
        with Session(db_engine) as db:
            db.execute(delete(models.Account).where(models.Account.id == account_id))
            db.commit()


def test_get_or_default_is_on_platform_for_cloud(
    db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRELOOP_HOSTED", "true")
    monkeypatch.delenv("PRELOOP_GOVERNANCE_ANALYSIS_MODEL", raising=False)

    row = crud_governance_review_settings.get_or_default(
        db_session, account_id=test_user.account_id
    )

    assert row.mode == "on_platform"
    assert (
        db_session.query(GovernanceReviewSettings)
        .filter(GovernanceReviewSettings.account_id == test_user.account_id)
        .count()
        == 0
    )


def test_get_or_default_is_off_for_self_hosted_without_a_model(
    db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRELOOP_HOSTED", "")
    monkeypatch.delenv("PRELOOP_GOVERNANCE_ANALYSIS_MODEL", raising=False)

    row = crud_governance_review_settings.get_or_default(
        db_session, account_id=test_user.account_id
    )

    assert row.mode == "off"


def test_get_or_default_uses_own_model_when_self_hosted_model_is_configured(
    db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRELOOP_HOSTED", "")
    monkeypatch.setenv("PRELOOP_GOVERNANCE_ANALYSIS_MODEL", "example-flash")

    row = crud_governance_review_settings.get_or_default(
        db_session, account_id=test_user.account_id
    )

    assert row.mode == "on_own_model"


def test_set_mode_persists_and_overrides_the_default(
    db_session: Session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRELOOP_HOSTED", "true")
    crud_governance_review_settings.set_mode(
        db_session,
        account_id=test_user.account_id,
        mode="off",
        mode_changed_by_id=test_user.id,
        commit=False,
    )

    row = crud_governance_review_settings.get_or_default(
        db_session, account_id=test_user.account_id
    )

    assert row.mode == "off"
    assert row.mode_changed_by_id == test_user.id


def test_upsert_digest_is_per_segment(db_session: Session, test_user: User) -> None:
    session = _session(db_session, test_user.account_id)
    start, end = _window()
    closed = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    first = crud_governance_session_digest.upsert(
        db_session,
        account_id=test_user.account_id,
        runtime_session_id=session.id,
        segment_index=0,
        window_start=start,
        window_end=closed,
        policy_document_version=1,
        digest={"intent": "first"},
        observations=[
            {"rule_ref": "must.secrets", "excerpt": "none", "confidence": 0.2}
        ],
        rolling_summary="Started a parser change.",
        commit=False,
    )
    replaced = crud_governance_session_digest.upsert(
        db_session,
        account_id=test_user.account_id,
        runtime_session_id=session.id,
        segment_index=0,
        window_start=start,
        window_end=closed,
        policy_document_version=2,
        summary={"intent": "revised"},
        commit=False,
    )
    second = crud_governance_session_digest.upsert(
        db_session,
        account_id=test_user.account_id,
        runtime_session_id=session.id,
        segment_index=1,
        window_start=closed,
        window_end=datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc),
        digest={"intent": "next week"},
        commit=False,
    )

    assert first.id == replaced.id
    assert replaced.policy_document_version == 2
    assert replaced.summary == {"intent": "revised"}
    assert replaced.digest == {"intent": "revised"}
    listed = crud_governance_session_digest.list_for_window(
        db_session,
        account_id=test_user.account_id,
        window_start=start,
        window_end=end,
    )
    assert [row.segment_index for row in listed] == [0]
    assert second.segment_index == 1


def test_digest_over_four_kibibytes_is_rejected(
    db_session: Session, test_user: User
) -> None:
    session = _session(db_session, test_user.account_id)
    start, end = _window()
    digest = {"intent": "x" * (DIGEST_MAX_BYTES + 1)}
    with pytest.raises(ValueError, match="4"):
        crud_governance_session_digest.upsert(
            db_session,
            account_id=test_user.account_id,
            runtime_session_id=session.id,
            window_start=start,
            window_end=end,
            digest=digest,
            commit=False,
        )


def test_recreating_a_run_for_the_same_week_returns_the_existing_run(
    db_session: Session, test_user: User
) -> None:
    start, end = _window()
    first = crud_governance_review_run.create_run(
        db_session,
        account_id=test_user.account_id,
        window_start=start,
        window_end=end,
        mode_before="on_platform",
        commit=False,
    )
    finished = crud_governance_review_run.finish_run(
        db_session,
        account_id=test_user.account_id,
        run_id=first.id,
        status="completed",
        findings_count=3,
        mode_after="on_platform",
        commit=False,
    )
    second = crud_governance_review_run.create_run(
        db_session,
        account_id=test_user.account_id,
        window_start=start,
        window_end=end,
        mode_before="off",
        commit=False,
    )

    assert second.id == first.id
    assert finished is not None
    assert finished.status == "completed"
    assert second.status == "completed"
    assert second.findings_count == 3
    assert second.mode_before == "on_platform"


def test_inserting_an_existing_fingerprint_is_a_no_op(
    db_session: Session, test_user: User
) -> None:
    start, end = _window()
    run = crud_governance_review_run.create_run(
        db_session,
        account_id=test_user.account_id,
        window_start=start,
        window_end=end,
        commit=False,
    )
    payload = _finding(run.id)
    inserted = crud_governance_finding.insert_findings(
        db_session,
        account_id=test_user.account_id,
        findings=[payload, payload],
        commit=False,
    )
    again = crud_governance_finding.insert_findings(
        db_session,
        account_id=test_user.account_id,
        findings=[payload],
        commit=False,
    )
    rows = crud_governance_finding.list_findings(
        db_session, account_id=test_user.account_id, run_id=run.id
    )

    assert len(inserted) == 1
    assert again == []
    assert len(rows) == 1
    assert rows[0].rule_ref == "prefer.tools"


def test_list_findings_filters_and_dismiss(
    db_session: Session, test_user: User
) -> None:
    start, end = _window()
    run = crud_governance_review_run.create_run(
        db_session,
        account_id=test_user.account_id,
        window_start=start,
        window_end=end,
        commit=False,
    )
    crud_governance_finding.insert_findings(
        db_session,
        account_id=test_user.account_id,
        findings=[
            _finding(run.id, "open"),
            {
                **_finding(run.id, "closed"),
                "kind": "violation",
                "severity": "high",
                "user_id": test_user.id,
            },
        ],
        commit=False,
    )
    closed = crud_governance_finding.list_findings(
        db_session,
        account_id=test_user.account_id,
        kind="violation",
        severity="high",
        user_id=test_user.id,
    )[0]
    crud_governance_finding.dismiss(
        db_session,
        account_id=test_user.account_id,
        finding_id=closed.id,
        dismissed_by_id=test_user.id,
        reason="Expected",
        commit=False,
    )

    open_rows = crud_governance_finding.list_findings(
        db_session, account_id=test_user.account_id, dismissed=False
    )
    dismissed = crud_governance_finding.list_findings(
        db_session, account_id=test_user.account_id, dismissed=True, run_id=run.id
    )

    assert [row.fingerprint for row in open_rows] == ["open"]
    assert [row.fingerprint for row in dismissed] == ["closed"]
    assert dismissed[0].dismissed_reason == "Expected"
    assert dismissed[0].dismissed_by_id == test_user.id
