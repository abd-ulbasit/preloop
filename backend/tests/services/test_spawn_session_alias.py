"""Spawn aliases distinguish workers without widening note scope (#1552)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from preloop.models.crud import crud_account, crud_managed_agent, crud_runtime_session
from preloop.services.agent_session_lineage import register_session_start
from preloop.services.agent_send_note import send_note_from_agent
from preloop.services.agent_session_list import list_for_agent
from preloop.schemas.operator_notes import OperatorNoteCreate


@pytest.fixture
def account(db_session):
    return crud_account.create(db_session, obj_in={"organization_name": "Workers"})


def start(db, account, external, alias=None, parent=None, principal="machine"):
    return register_session_start(
        db,
        account_id=account.id,
        principal_type="claude_code",
        principal_id=principal,
        principal_name="Worker",
        external_session_id=external,
        alias=alias,
        parent_session_id=parent,
    )


def test_alias_write_once_and_title(db_session, account):
    first = start(db_session, account, "worker", "brief-1552")
    again = start(db_session, account, "worker", "changed")
    assert again.alias == first.alias == "brief-1552"
    row = crud_runtime_session.get_account_session(
        db_session,
        account_id=account.id,
        runtime_session_id=UUID(first.runtime_session_id),
    )
    assert row.title == "brief-1552"


def test_collision_suffix_and_reuse_after_end(db_session, account):
    first = start(db_session, account, "one", "brief")
    assert start(db_session, account, "two", "brief").alias == "brief-2"
    row = crud_runtime_session.get_account_session(
        db_session,
        account_id=account.id,
        runtime_session_id=UUID(first.runtime_session_id),
    )
    row.ended_at = datetime.now(UTC)
    db_session.commit()
    assert start(db_session, account, "three", "brief").alias == "brief"


def test_alias_sanitized_and_capped(db_session, account):
    result = start(db_session, account, "unsafe", "<bad> name/" + "a" * 100)
    assert len(result.alias) == 64
    assert all(c.isascii() and (c.isalnum() or c in "._-") for c in result.alias)


def test_collision_namespace_is_parent_or_principal(db_session, account):
    p1 = start(db_session, account, "parent-one")
    p2 = start(db_session, account, "parent-two")
    assert (
        start(db_session, account, "one", "brief", p1.runtime_session_id).alias
        == "brief"
    )
    assert (
        start(db_session, account, "two", "brief", p1.runtime_session_id, "other").alias
        == "brief-2"
    )
    assert (
        start(db_session, account, "three", "brief", p2.runtime_session_id).alias
        == "brief"
    )
    assert (
        start(db_session, account, "four", "brief", principal="other").alias == "brief"
    )


def test_four_workers_list_and_steer_individually(db_session, account):
    parent = start(db_session, account, "parent")
    agent = crud_managed_agent.create_custom_agent(
        db_session, account_id=account.id, display_name="Conductor", commit=True
    )
    workers = [
        start(
            db_session, account, f"worker-{i}", f"brief-{i}", parent.runtime_session_id
        )
        for i in range(4)
    ]
    listed = list_for_agent(
        db_session,
        account_id=account.id,
        managed_agent_id=agent.id,
        caller_session_ids=[parent.runtime_session_id],
        subject_context={},
        alias="brief-2",
    )
    assert [r["alias"] for r in listed["results"]] == ["brief-2"]
    rest = crud_runtime_session.list_account_sessions(
        db_session, account_id=str(account.id), alias="brief-2"
    )
    assert [r["alias"] for r in rest["items"]] == ["brief-2"]
    for worker in workers:
        result = send_note_from_agent(
            db_session,
            account_id=str(account.id),
            author_agent_id=agent.id,
            text="Report progress",
            alias=worker.alias,
            author_session_ids=[parent.runtime_session_id],
            author_principal=("claude_code", "machine"),
        )
        assert result["ok"], result
        assert result["note"]["runtime_session_id"] == worker.runtime_session_id


def test_alias_ambiguity_principal_preference_and_scope(db_session, account):
    first = start(db_session, account, "one", "brief", principal="one")
    start(db_session, account, "two", "brief", principal="two")
    agent = crud_managed_agent.create_custom_agent(
        db_session, account_id=account.id, display_name="Caller", commit=True
    )
    params = dict(
        account_id=str(account.id),
        author_agent_id=agent.id,
        text="Hello",
        alias="brief",
    )
    ambiguous = send_note_from_agent(db_session, **params)
    assert ambiguous["error"]["code"] == "target_ambiguous"
    scoped = send_note_from_agent(
        db_session, **params, author_principal=("claude_code", "one")
    )
    assert not scoped["ok"]
    assert scoped["error"]["code"].startswith("note_scope_")
    assert first.runtime_session_id


def test_cross_account_alias_is_not_found(db_session, account):
    other = crud_account.create(db_session, obj_in={"organization_name": "Other"})
    start(db_session, other, "foreign", "foreign")
    agent = crud_managed_agent.create_custom_agent(
        db_session, account_id=account.id, display_name="Caller", commit=True
    )
    result = send_note_from_agent(
        db_session,
        account_id=str(account.id),
        author_agent_id=agent.id,
        text="Hello",
        alias="foreign",
    )
    assert result["error"]["code"] == "target_not_found"


@pytest.mark.parametrize(
    "target",
    [
        {"alias": "brief"},
        {"external_session_id": "worker"},
        {
            "children": "all",
            "parent_session_id": "11111111-2222-3333-4444-555555555555",
        },
    ],
)
def test_rest_accepts_target_parity(target):
    payload = OperatorNoteCreate(text="Hello", **target)
    assert payload.model_dump()[next(iter(target))] == next(iter(target.values()))


def test_concurrent_workers_get_distinct_aliases(db_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from preloop.models.models.account import Account

    with Session(db_engine) as db:
        account = crud_account.create(
            db, obj_in={"organization_name": "Concurrent workers"}
        )
        db.commit()
        account_id = account.id
    barrier = Barrier(4)

    def spawn(i):
        with Session(db_engine) as db:
            barrier.wait(timeout=10)
            return register_session_start(
                db,
                account_id=account_id,
                principal_type="claude_code",
                principal_id="concurrent",
                principal_name="Worker",
                external_session_id=f"concurrent-{i}",
                alias="brief",
            ).alias

    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            aliases = list(workers.map(spawn, range(4)))
        assert set(aliases) == {"brief", "brief-2", "brief-3", "brief-4"}
    finally:
        with Session(db_engine) as db:
            db.query(Account).filter(Account.id == account_id).delete()
            db.commit()


def test_reused_alias_refuses_resume_without_renaming(db_session, account):
    from preloop.models.crud.runtime_session import SpawnAliasConflictError

    first = start(db_session, account, "old-worker", "brief")
    row = crud_runtime_session.get_account_session(
        db_session,
        account_id=account.id,
        runtime_session_id=UUID(first.runtime_session_id),
    )
    row.ended_at = datetime.now(UTC)
    db_session.commit()
    start(db_session, account, "new-worker", "brief")
    with pytest.raises(SpawnAliasConflictError):
        start(db_session, account, "old-worker", "brief")
    db_session.rollback()


def test_alias_fills_without_replacing_an_existing_title(db_session, account):
    first = start(db_session, account, "titled-worker")
    row = crud_runtime_session.get_account_session(
        db_session,
        account_id=account.id,
        runtime_session_id=UUID(first.runtime_session_id),
    )
    row.title = "Existing human title"
    db_session.commit()
    assert start(db_session, account, "titled-worker", "brief").alias == "brief"
    assert row.title == "Existing human title"


def test_long_alias_collision_keeps_suffix_within_cap(db_session, account):
    label = "a" * 64
    assert start(db_session, account, "long-one", label).alias == label
    assert start(db_session, account, "long-two", label).alias == "a" * 62 + "-2"


def test_principal_preference_reaches_owned_child(db_session, account):
    parent = start(db_session, account, "own-parent")
    child = start(
        db_session,
        account,
        "own-child",
        "brief",
        parent.runtime_session_id,
        principal="one",
    )
    start(db_session, account, "other-child", "brief", principal="two")
    agent = crud_managed_agent.create_custom_agent(
        db_session, account_id=account.id, display_name="Conductor", commit=True
    )
    result = send_note_from_agent(
        db_session,
        account_id=str(account.id),
        author_agent_id=agent.id,
        text="Report",
        alias="brief",
        author_principal=("claude_code", "one"),
        author_session_ids=[parent.runtime_session_id],
    )
    assert result["ok"], result
    assert result["note"]["runtime_session_id"] == child.runtime_session_id
