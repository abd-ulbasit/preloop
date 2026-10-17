"""Synthetic PostgreSQL acceptance of fencing, durable delivery and replica leases."""

from __future__ import annotations
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from preloop.models import models
from preloop.models.crud import crud_support, crud_chat
from preloop.models.crud.support import SupportConflictError


@pytest.fixture
def support_db():
    url = os.getenv("CHAT_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set CHAT_TEST_DATABASE_URL to an isolated migrated synthetic database"
        )
    engine = create_engine(url)
    with Session(engine) as db:
        row = crud_support.conversations.create(
            db,
            obj_in={
                "access_digest": uuid4().hex,
                "source_page": "pricing",
                "expires_at": datetime.utcnow() + timedelta(days=1),
            },
        )
        yield db, row, engine
        db.rollback()
        crud_support.conversations.delete(db, id=row.id)
    engine.dispose()


def user_message(db, conversation):
    return crud_support.append_user(
        db, conversation.id, event_id=uuid4().hex, content="encrypted-question"
    )


def test_takeover_fences_inference_already_in_flight(support_db) -> None:
    db, conversation, _ = support_db
    user_message(db, conversation)
    job = crud_support.claim(db, "inference")
    assert job is not None
    crud_support.conversations.update(
        db, db_obj=conversation, obj_in={"operator_root_id": uuid4().hex}
    )
    crud_support.operator_event(
        db,
        conversation.operator_root_id,
        "operator",
        "takeover-post",
        "encrypted-status",
        "takeover",
    )
    assert not crud_support.stage_answer(db, job, "late-answer", [])
    row, messages = crud_support.history(db, conversation.id)
    assert row.status == "human"
    assert all(message.role != "assistant" for message in messages)


def test_takeover_fences_generated_answer_before_browser_delivery(support_db) -> None:
    db, conversation, _ = support_db
    user_message(db, conversation)
    job = crud_support.claim(db, "inference")
    assert crud_support.stage_answer(db, job, "generated-answer", [])
    crud_support.conversations.update(
        db, db_obj=conversation, obj_in={"operator_root_id": uuid4().hex}
    )
    crud_support.operator_event(
        db,
        conversation.operator_root_id,
        "operator",
        "takeover-post",
        "status",
        "takeover",
    )
    _, messages = crud_support.history(db, conversation.id)
    assert all(message.role != "assistant" for message in messages)
    assert not crud_support.outbox.get_multi(
        db, message_id=job.message_id, kind="notify", status="sent"
    )


def test_resume_is_explicit_and_operator_reply_is_deduplicated(support_db) -> None:
    db, conversation, _ = support_db
    user_message(db, conversation)
    root = uuid4().hex
    crud_support.conversations.update(
        db, db_obj=conversation, obj_in={"operator_root_id": root}
    )
    with pytest.raises(SupportConflictError, match="Take over"):
        crud_support.operator_event(db, root, "operator", "reply-first", "reply")
    db.rollback()
    crud_support.operator_event(db, root, "operator", "takeover", "status", "takeover")
    with pytest.raises(SupportConflictError, match="Another operator"):
        crud_support.operator_event(db, root, "wrong", "wrong-reply", "reply")
    db.rollback()
    for _ in range(2):
        crud_support.operator_event(db, root, "operator", "reply", "human answer")
    _, messages = crud_support.history(db, conversation.id)
    assert [message.content_encrypted for message in messages].count(
        "human answer"
    ) == 1
    user_message(db, conversation)
    assert crud_support.claim(db, "inference") is None
    crud_support.operator_event(db, root, "operator", "resume", "status", "resume")
    user_message(db, conversation)
    assert crud_support.claim(db, "inference") is not None


def test_close_requires_explicit_reopen(support_db) -> None:
    db, conversation, _ = support_db
    root = uuid4().hex
    crud_support.conversations.update(
        db, db_obj=conversation, obj_in={"operator_root_id": root}
    )
    crud_support.operator_event(db, root, "operator", "close", "status", "close")
    with pytest.raises(SupportConflictError, match="closed"):
        user_message(db, conversation)
    db.rollback()
    with pytest.raises(SupportConflictError, match="explicitly reopen"):
        crud_support.operator_event(db, root, "operator", "resume", "status", "resume")
    db.rollback()
    crud_support.operator_event(db, root, "operator", "reopen", "status", "reopen")
    assert crud_support.conversations.get(db, conversation.id).status == "human"


def test_user_retries_create_one_notification_and_root_send_is_serial(
    support_db,
) -> None:
    db, conversation, _ = support_db
    event = uuid4().hex
    one = crud_support.append_user(
        db, conversation.id, event_id=event, content="question"
    )
    again = crud_support.append_user(
        db, conversation.id, event_id=event, content="question"
    )
    assert one.id == again.id
    user_message(db, conversation)
    first = crud_support.claim(db, "notify")
    assert first is not None
    assert crud_support.claim(db, "notify") is None
    assert crud_support.transition(db, first, "sending")
    crud_support.sent(db, first, "provider-root", is_root=True)
    next_job = crud_support.claim(db, "notify")
    assert next_job is not None
    assert (
        crud_support.conversations.get(db, conversation.id).operator_root_id
        == "provider-root"
    )


def test_ambiguous_send_cannot_be_claimed_again(support_db) -> None:
    db, conversation, _ = support_db
    user_message(db, conversation)
    first = crud_support.claim(db, "notify")
    assert crud_support.transition(db, first, "sending")
    crud_support.outbox.update(
        db,
        db_obj=first,
        obj_in={"lease_until": datetime.utcnow() - timedelta(seconds=1)},
    )
    assert crud_support.claim(db, "notify") is None
    assert crud_support.outbox.get(db, first.id).status == "uncertain"


def test_model_budget_is_atomic_across_replicas(support_db) -> None:
    db, _, engine = support_db
    bucket = "test-budget:" + uuid4().hex

    def reserve(_: int) -> bool:
        with Session(engine) as other:
            return crud_support.reserve(
                other, bucket, request_limit=100, amount=60, budget=100
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == [False, True]
    quota = db.query(models.SupportQuota).filter_by(bucket=bucket).one()
    db.delete(quota)
    db.commit()


def test_mattermost_listener_has_only_one_replica_owner(support_db) -> None:
    db, _, engine = support_db
    account = models.Account(organization_name="Synthetic listener")
    db.add(account)
    db.flush()
    connection = models.ChatConnection(
        account_id=account.id,
        provider="mattermost",
        workspace_id="team",
        name="Synthetic bot",
        credentials_encrypted="encrypted",
    )
    db.add(connection)
    db.commit()
    connection_id, account_id = connection.id, account.id
    try:

        def claim(token: str) -> bool:
            with Session(engine) as other:
                return crud_chat.claim_listener(other, connection_id, token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(claim, [str(uuid4()), str(uuid4())])) == [
                False,
                True,
            ]
        listener = crud_chat.listeners.get_multi(db, connection_id=connection_id)[0]
        assert not crud_chat.listener_health(
            db, connection_id, "wrong-token", "connected"
        )
        listener.lease_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        assert claim("new-owner")
    finally:
        db.query(models.Account).filter_by(id=account_id).delete()
        db.commit()


def test_stale_operator_listener_cannot_change_ownership(support_db) -> None:
    db, conversation, _ = support_db
    root = uuid4().hex
    crud_support.conversations.update(
        db, db_obj=conversation, obj_in={"operator_root_id": root}
    )
    with pytest.raises(SupportConflictError, match="lease changed"):
        crud_support.operator_event(
            db,
            root,
            "operator",
            "stale-takeover",
            "status",
            "takeover",
            listener_token="expired-replica",
        )
    db.rollback()
    assert crud_support.conversations.get(db, conversation.id).status == "assistant"


def test_failed_notification_blocks_followups_until_explicit_retry(support_db) -> None:
    db, conversation, _ = support_db
    user_message(db, conversation)
    user_message(db, conversation)
    job = crud_support.claim(db, "notify")
    assert crud_support.transition(db, job, "failed")
    assert crud_support.claim(db, "notify") is None
    crud_support.retry_notification(db, conversation.id, job.id)
    retried = crud_support.claim(db, "notify")
    assert retried.id == job.id
    assert crud_support.transition(db, retried, "uncertain")
    with pytest.raises(SupportConflictError, match="reconcile uncertain"):
        crud_support.retry_notification(db, conversation.id, job.id)
