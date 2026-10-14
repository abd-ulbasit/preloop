"""GET /api/v1/policies/notices/summary feeds the Attention card (#959)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from uuid import uuid4

from preloop.models.crud import crud_account, crud_policy_notice_hit, crud_user
from preloop.models.models.user import User
from preloop.services.policy_notices import PolicyNotice, record_policy_notice


def _record(db: Session, account_id, user_id, rule_id: str, at: datetime) -> str:
    outcome = record_policy_notice(
        db,
        PolicyNotice(
            account_id=account_id,
            user_id=user_id,
            target="model.response",
            rule_id=rule_id,
            rule_description=None,
            text_sha256="d" * 64,
            excerpt=f"excerpt for {rule_id}",
        ),
        now=at,
        deliver=lambda _db, _hit: {},
    )
    return str(outcome.hit_id)


def test_summary_lists_rules_with_latest_hit(
    client: TestClient, db_session: Session, test_user: User
) -> None:
    now = datetime.now(timezone.utc)
    _record(
        db_session,
        test_user.account_id,
        test_user.id,
        "notify-a",
        now - timedelta(hours=3),
    )
    latest = _record(
        db_session,
        test_user.account_id,
        test_user.id,
        "notify-a",
        now - timedelta(hours=1),
    )
    _record(
        db_session, test_user.account_id, None, "notify-b", now - timedelta(hours=2)
    )

    response = client.get("/api/v1/policies/notices/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 7
    rules = {row["rule_id"]: row for row in body["rules"]}
    assert rules["notify-a"]["count"] == 2
    assert rules["notify-a"]["last_hit_id"] == latest
    assert rules["notify-a"]["last_excerpt"] == "excerpt for notify-a"
    assert rules["notify-a"]["last_username"] == "testuser"
    assert rules["notify-b"]["last_username"] is None
    assert body["rules"][0]["rule_id"] == "notify-a"


def test_summary_is_scoped_to_the_account(
    client: TestClient, db_session: Session, test_user: User
) -> None:
    other = crud_account.create(
        db_session, obj_in={"organization_name": "Other", "is_active": True}
    )
    _record(db_session, other.id, None, "notify-elsewhere", datetime.now(timezone.utc))

    response = client.get("/api/v1/policies/notices/summary")

    assert response.status_code == 200
    assert response.json()["rules"] == []


def _user(db: Session, account_id, username: str) -> User:
    return crud_user.create(
        db,
        obj_in={
            "account_id": account_id,
            "email": f"{username}@example.com",
            "username": username,
            "full_name": username,
            "is_active": True,
            "email_verified": True,
            "hashed_password": "x",
            "user_source": "local",
        },
    )


def test_group_by_user_counts_sum_to_rule_totals(
    client: TestClient, db_session: Session, test_user: User
) -> None:
    other = _user(db_session, test_user.account_id, "otherdev")
    now = datetime.now(timezone.utc)
    shared = {
        "account_id": test_user.account_id,
        "target": "model.request",
        "rule_description": None,
        "text_sha256": "a" * 64,
        "excerpt": "project-x",
        "model_alias": "flash-test",
        "agent_ref": "codex:agent-1",
        "now": now,
    }
    crud_policy_notice_hit.record(
        db_session,
        user_id=test_user.id,
        rule_id="notify-a",
        action="notify",
        **shared,
    )
    crud_policy_notice_hit.record(
        db_session,
        user_id=test_user.id,
        rule_id="deny-a",
        action="deny",
        audit_event_id=uuid4(),
        **shared,
    )
    crud_policy_notice_hit.record(
        db_session,
        user_id=other.id,
        rule_id="deny-a",
        action="deny",
        **shared,
    )
    db_session.commit()

    default = client.get("/api/v1/policies/notices/summary")
    assert default.status_code == 200
    default_body = default.json()
    assert default_body["group_by"] == "rule"
    assert [row["rule_id"] for row in default_body["rules"]] == ["notify-a"]
    assert {row["key"] for row in default_body["models"]} == {"flash-test"}
    assert {row["key"] for row in default_body["agents"]} == {"codex:agent-1"}

    grouped = client.get("/api/v1/policies/notices/summary?group_by=user")
    assert grouped.status_code == 200
    body = grouped.json()
    rule_total = sum(row["count"] for row in body["rules"])
    user_total = sum(row["count"] for row in body["users"])
    assert rule_total == user_total == 3
    users = {row["username"]: row["count"] for row in body["users"]}
    assert users["testuser"] == 2
    assert users["otherdev"] == 1


def test_summary_rejects_unknown_group_by(client: TestClient) -> None:
    assert (
        client.get("/api/v1/policies/notices/summary?group_by=team").status_code == 422
    )


def test_summary_window_is_bounded(client: TestClient) -> None:
    assert client.get("/api/v1/policies/notices/summary?days=0").status_code == 422
    assert client.get("/api/v1/policies/notices/summary?days=91").status_code == 422
