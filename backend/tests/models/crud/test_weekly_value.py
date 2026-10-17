"""Real PostgreSQL account isolation, publication cache upserts and weekly bounds."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_account, crud_api_usage, crud_runtime_session
from preloop.models.crud import weekly_value as crud
from preloop.models.crud.runtime_session import INTERNAL_USAGE_PURPOSES

END = datetime(2026, 10, 17, tzinfo=UTC)
START = END - timedelta(days=7)
PR = "https://github.com/acme/repo/pull/12"


def test_cache_is_account_scoped_and_preserves_existing_attribution(
    db_session, test_user
) -> None:
    account = test_user.account_id
    other = crud_account.create(
        db_session, obj_in={"organization_name": "Other weekly value"}
    )
    original = models.IssueCostPullRequest(
        account_id=account, pr_key=PR, ambiguous=True
    )
    db_session.add(original)
    db_session.flush()
    for aid, title in ((account, "mine"), (other.id, "theirs")):
        crud.store(
            db_session, account_id=aid, pr_key=PR, evidence={"title": title}, now=END
        )
    db_session.expire_all()
    mine = crud.cached(db_session, account_id=account, pr_keys=[PR])[PR]
    assert mine.value_evidence == {"title": "mine"}
    assert mine.ambiguous is True
    crud.store(
        db_session,
        account_id=account,
        pr_key=PR,
        evidence={"title": "updated"},
        now=END,
    )
    assert (
        len(
            db_session.scalars(
                select(models.IssueCostPullRequest).where(
                    models.IssueCostPullRequest.account_id == account
                )
            ).all()
        )
        == 1
    )
    db_session.expire_all()
    assert crud.cached(db_session, account_id=other.id, pr_keys=[PR])[
        PR
    ].value_evidence == {"title": "theirs"}


def test_candidates_half_open_window_and_unknown_cost(
    db_session: Session, test_user: models.User
) -> None:
    account = test_user.account_id
    flow = models.Flow(
        name="Outcome flow", account_id=account, prompt_template="work", agent_config={}
    )
    db_session.add(flow)
    db_session.flush()
    for stamp in (START, END - timedelta(seconds=1), END, START - timedelta(seconds=1)):
        db_session.add(
            models.FlowExecution(
                flow_id=flow.id,
                status="COMPLETED",
                start_time=(stamp - timedelta(hours=1)).replace(tzinfo=None),
                end_time=stamp.replace(tzinfo=None),
                result={"pr_url": PR},
            )
        )
    session = crud_runtime_session.upsert_by_source(
        db_session,
        account_id=account,
        session_source_type="codex",
        session_source_id=str(uuid4()),
        started_at=START,
        last_activity_at=START,
    )
    crud_api_usage.log_gateway_request(
        db_session,
        endpoint="/openai/v1/responses",
        method="POST",
        status_code=200,
        duration=1,
        account_id=str(account),
        runtime_session_id=str(session.id),
        estimated_cost=1,
    )
    crud_api_usage.log_gateway_request(
        db_session,
        endpoint="/openai/v1/responses",
        method="POST",
        status_code=200,
        duration=1,
        account_id=str(account),
        runtime_session_id=str(session.id),
        estimated_cost=None,
    )
    db_session.flush()
    candidates = crud.candidates(db_session, account_id=account, start=START, end=END)
    assert len([r for r in candidates if r["kind"] == "flow"]) == 2
    assert all(r["cost"] is None for r in candidates if r["kind"] == "flow")
    assert next(r for r in candidates if r["id"] == str(session.id))["cost"] is None
    assert crud.candidates(db_session, account_id=uuid4(), start=START, end=END) == []


def test_candidate_cost_excludes_replay_and_internal_traffic(
    db_session, test_user
) -> None:
    flow = models.Flow(
        name="Cost evidence",
        account_id=test_user.account_id,
        prompt_template="work",
        agent_config={},
    )
    db_session.add(flow)
    db_session.flush()
    execution = models.FlowExecution(
        flow_id=flow.id,
        status="COMPLETED",
        start_time=START.replace(tzinfo=None),
        end_time=(END - timedelta(seconds=1)).replace(tzinfo=None),
    )
    db_session.add(execution)
    db_session.flush()
    session = crud_runtime_session.upsert_by_source(
        db_session,
        account_id=test_user.account_id,
        session_source_type="codex",
        session_source_id=str(uuid4()),
        started_at=START,
        last_activity_at=START,
    )
    for purpose, cost in (
        (None, 1),
        ("replay_validation", 100),
        (INTERNAL_USAGE_PURPOSES[0], None),
        (INTERNAL_USAGE_PURPOSES[-1], 50),
    ):
        crud_api_usage.log_gateway_request(
            db_session,
            endpoint="/openai/v1/responses",
            method="POST",
            status_code=200,
            duration=1,
            account_id=str(test_user.account_id),
            runtime_session_id=str(session.id),
            flow_execution_id=str(execution.id),
            estimated_cost=cost,
            meta_data={"purpose": purpose} if purpose else None,
        )
    db_session.flush()
    rows = crud.candidates(
        db_session, account_id=test_user.account_id, start=START, end=END
    )
    assert {r["id"]: r["cost"] for r in rows} == {
        str(execution.id): 1,
        str(session.id): 1,
    }


def test_repository_contexts_and_recorded_prs_are_account_scoped(
    db_session, test_user
) -> None:
    other = crud_account.create(db_session, obj_in={"organization_name": "Other forge"})
    for aid in (test_user.account_id, other.id):
        tracker = models.Tracker(name="Forge", tracker_type="github", account_id=aid)
        db_session.add(tracker)
        db_session.flush()
        org = models.Organization(name="Org", identifier="org", tracker_id=tracker.id)
        db_session.add(org)
        db_session.flush()
        db_session.add(
            models.Project(name="Repo", identifier="repo", organization_id=org.id)
        )
        db_session.add(
            models.IssueCostPullRequest(
                account_id=aid,
                pr_key=PR,
                opened_at=START - timedelta(days=1),
                merged_at=START,
            )
        )
    db_session.add(
        models.IssueCostPullRequest(
            account_id=test_user.account_id,
            pr_key=PR + "0",
            opened_at=END,
            merged_at=END,
        )
    )
    db_session.flush()
    contexts = crud.repository_contexts(db_session, account_id=test_user.account_id)
    assert len(contexts) == 1
    assert contexts[0][2].account_id == test_user.account_id
    assert crud.recorded_prs(
        db_session, account_id=test_user.account_id, start=START, end=END
    ) == [PR]


def test_commit_cache_preserves_loaded_accounts(db_session, test_user) -> None:
    from sqlalchemy import inspect

    account = crud_account.get(db_session, id=test_user.account_id)
    original_expiry = db_session.expire_on_commit
    crud.commit_evidence(db_session)
    assert not inspect(account).expired
    assert db_session.expire_on_commit is original_expiry


def test_commit_cache_rolls_back_and_reraises_failed_transaction(
    db_session, monkeypatch
) -> None:
    from unittest.mock import Mock

    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError

    original_expiry = db_session.expire_on_commit
    rollback = Mock(wraps=db_session.rollback)

    def fail_commit() -> None:
        db_session.execute(text("SELECT * FROM no_such_weekly_value_commit_table"))

    monkeypatch.setattr(db_session, "rollback", rollback)
    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(ProgrammingError):
        crud.commit_evidence(db_session)
    rollback.assert_called_once_with()
    assert db_session.expire_on_commit is original_expiry
    assert db_session.is_active
    assert isinstance(crud_account.get_multi(db_session, limit=1), list)
