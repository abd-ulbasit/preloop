"""Batched AI verdicts for the Duplicates page: one fast call per page."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from preloop.api.endpoints import issue_duplicates as mod
from preloop.schemas.issue_duplicate import IssueDuplicateVerdictBatchRequest


@pytest.fixture(autouse=True)
def _clean_state():
    mod._verdict_inflight.clear()
    mod._verdict_failures.clear()
    yield
    mod._verdict_inflight.clear()
    mod._verdict_failures.clear()


class _Db:
    """Session stand-in; ``owned`` is what the ownership lookup answers."""

    def __init__(self, owned):
        self.owned = owned


@pytest.fixture(autouse=True)
def _owned_lookup(monkeypatch):
    monkeypatch.setattr(
        mod.crud_issue,
        "get_owned_ids",
        lambda db, ids, account_id: {i for i in db.owned if i in {str(x) for x in ids}},
    )


@pytest.fixture
def claim(monkeypatch):
    """Replace the cross-replica advisory lock; ``state['free']`` decides it."""
    state = {"free": True}

    @contextmanager
    def _claim(key):
        yield state["free"]

    monkeypatch.setattr(mod, "_pair_check_claim", _claim)
    return state


def _row(a, b, decision="duplicate"):
    low, high = sorted((a, b), key=str)
    return SimpleNamespace(
        issue1_id=low,
        issue2_id=high,
        decision=decision,
        decision_at=None,
        reason="same bug",
        suggestion=None,
        resolution=None,
        resolution_at=None,
        resolution_reason=None,
        resulting_issue1_id=None,
        resulting_issue2_id=None,
        ai_model_id=None,
        ai_model_name="m",
    )


def _call(pairs, db, *, analyze=True, retry=False, account_id="acc"):
    body = IssueDuplicateVerdictBatchRequest(
        pairs=[{"issue1_id": a, "issue2_id": b} for a, b in pairs],
        analyze=analyze,
        retry=retry,
    )
    return mod.get_issue_duplicate_verdicts(
        body=body,
        db=db,
        current_user=SimpleNamespace(account_id=account_id),
        settings=SimpleNamespace(),
    )


@pytest.fixture
def scheduled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mod, "_schedule_verdict_check", lambda key, *a: calls.append(key)
    )
    return calls


def test_stored_verdicts_come_back_in_one_lookup(monkeypatch, scheduled):
    a, b, c, d = (uuid4() for _ in range(4))
    lookup = MagicMock(return_value=[_row(b, a), _row(c, d, "unrelated")])
    monkeypatch.setattr(mod.crud_issue_duplicate, "get_many_by_pairs", lookup)
    model = MagicMock()
    monkeypatch.setattr(mod.crud_ai_model, "get_default_active_model", model)

    result = _call([(a, b), (c, d)], _Db(set()))

    assert lookup.call_count == 1
    assert [i.state for i in result.items] == ["done", "done"]
    assert result.items[1].verdict.decision == "unrelated"
    assert result.pending == 0
    model.assert_not_called()
    assert scheduled == []


def test_new_pairs_are_pending_and_checked_in_the_background(monkeypatch, scheduled):
    a, b = uuid4(), uuid4()
    monkeypatch.setattr(
        mod.crud_issue_duplicate, "get_many_by_pairs", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        mod.crud_ai_model, "get_default_active_model", MagicMock(return_value="m")
    )

    result = _call([(a, b)], _Db({str(a), str(b)}))

    assert result.items[0].state == "pending"
    assert result.pending == 1
    assert scheduled == [mod._pair_key("acc", a, b)]


def test_read_only_call_does_not_start_checks(monkeypatch, scheduled):
    a, b = uuid4(), uuid4()
    monkeypatch.setattr(
        mod.crud_issue_duplicate, "get_many_by_pairs", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        mod.crud_ai_model, "get_default_active_model", MagicMock(return_value="m")
    )
    result = _call([(a, b)], _Db({str(a), str(b)}), analyze=False)
    assert result.items[0].state == "pending"
    assert scheduled == []


def test_no_default_model_starts_nothing(monkeypatch, scheduled):
    a, b = uuid4(), uuid4()
    monkeypatch.setattr(
        mod.crud_issue_duplicate, "get_many_by_pairs", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        mod.crud_ai_model, "get_default_active_model", MagicMock(return_value=None)
    )
    result = _call([(a, b)], _Db({str(a), str(b)}))
    assert result.items[0].state == "no_model"
    assert result.pending == 0
    assert scheduled == []


def test_pairs_outside_the_account_are_not_found(monkeypatch, scheduled):
    a, b, foreign = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(
        mod.crud_issue_duplicate, "get_many_by_pairs", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        mod.crud_ai_model, "get_default_active_model", MagicMock(return_value="m")
    )
    result = _call([(a, foreign), (a, a)], _Db({str(a), str(b)}))
    assert [i.state for i in result.items] == ["not_found", "not_found"]
    assert scheduled == []


def test_failures_are_reported_until_retry(monkeypatch, scheduled):
    a, b = uuid4(), uuid4()
    monkeypatch.setattr(
        mod.crud_issue_duplicate, "get_many_by_pairs", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        mod.crud_ai_model, "get_default_active_model", MagicMock(return_value="m")
    )
    key = mod._pair_key("acc", a, b)
    mod._verdict_failures[key] = ("rate_limited", mod.time.monotonic())

    failed = _call([(a, b)], _Db({str(a), str(b)}))
    assert failed.items[0].state == "failed"
    assert failed.items[0].error_code == "rate_limited"
    assert scheduled == []

    retried = _call([(a, b)], _Db({str(a), str(b)}), retry=True)
    assert retried.items[0].state == "pending"
    assert scheduled == [key]


def test_old_failures_expire(monkeypatch):
    key = ("acc", "a", "b")
    mod._verdict_failures[key] = ("ai_model_error", mod.time.monotonic() - 301)
    assert mod._recent_failure(key) is None
    assert key not in mod._verdict_failures


def test_schedule_runs_each_pair_once_at_a_time(monkeypatch):
    submitted = []

    class _Executor:
        def submit(self, fn, *args):
            submitted.append(args[0])
            return mod.Future()

    monkeypatch.setattr(mod, "_get_verdict_executor", lambda: _Executor())
    key = ("acc", "a", "b")
    mod._schedule_verdict_check(key, "acc", "a", "b", None)
    mod._schedule_verdict_check(key, "acc", "a", "b", None)
    assert submitted == [key]


def test_background_check_stores_a_verdict(monkeypatch, claim):
    db = MagicMock()
    monkeypatch.setattr(mod, "_open_verdict_session", lambda: db)
    monkeypatch.setattr(
        mod.crud_issue_duplicate, "get_by_issue_ids", MagicMock(return_value=None)
    )
    monkeypatch.setattr(
        mod.crud_issue, "get", MagicMock(side_effect=lambda db, id, account_id: id)
    )
    monkeypatch.setattr(
        mod.crud_ai_model, "get_default_active_model", MagicMock(return_value="m")
    )
    classify = MagicMock()
    monkeypatch.setattr(mod, "_classify_and_store", classify)
    key = ("acc", "a", "b")
    mod._verdict_inflight[key] = mod.Future()

    mod._run_verdict_check(key, "acc", "a", "b", "settings")

    classify.assert_called_once_with(
        db, issue1="a", issue2="b", default_model="m", settings="settings"
    )
    assert key not in mod._verdict_inflight
    db.close.assert_called_once()


def test_background_check_skips_a_pair_stored_meanwhile(monkeypatch, claim):
    db = MagicMock()
    monkeypatch.setattr(mod, "_open_verdict_session", lambda: db)
    monkeypatch.setattr(
        mod.crud_issue_duplicate, "get_by_issue_ids", MagicMock(return_value=object())
    )
    classify = MagicMock()
    monkeypatch.setattr(mod, "_classify_and_store", classify)
    mod._run_verdict_check(("acc", "a", "b"), "acc", "a", "b", None)
    classify.assert_not_called()


@pytest.mark.parametrize(
    "exc, code",
    [
        (HTTPException(429, "AI model is rate limited; retry later."), "rate_limited"),
        (
            HTTPException(500, {"code": "ai_model_error", "message": "x"}),
            "ai_model_error",
        ),
        (HTTPException(500, "OpenAI API key not configured."), "not_configured"),
        (RuntimeError("boom"), "ai_model_error"),
    ],
)
def test_background_failure_is_remembered(monkeypatch, claim, exc, code):
    db = MagicMock()
    monkeypatch.setattr(mod, "_open_verdict_session", lambda: db)
    monkeypatch.setattr(
        mod.crud_issue_duplicate, "get_by_issue_ids", MagicMock(return_value=None)
    )
    monkeypatch.setattr(mod.crud_issue, "get", MagicMock(return_value=object()))
    monkeypatch.setattr(
        mod.crud_ai_model, "get_default_active_model", MagicMock(return_value="m")
    )
    monkeypatch.setattr(mod, "_classify_and_store", MagicMock(side_effect=exc))
    key = ("acc", "a", "b")
    mod._run_verdict_check(key, "acc", "a", "b", None)
    assert mod._recent_failure(key) == code


def test_batch_size_is_bounded():
    with pytest.raises(ValueError):
        IssueDuplicateVerdictBatchRequest(
            pairs=[{"issue1_id": uuid4(), "issue2_id": uuid4()} for _ in range(51)]
        )


def test_pair_claimed_by_another_replica_is_not_checked_here(monkeypatch, claim):
    claim["free"] = False
    classify = MagicMock()
    monkeypatch.setattr(mod, "_classify_and_store", classify)
    opened = MagicMock()
    monkeypatch.setattr(mod, "_open_verdict_session", opened)
    key = ("acc", "a", "b")
    mod._verdict_inflight[key] = mod.Future()
    mod._run_verdict_check(key, "acc", "a", "b", None)
    classify.assert_not_called()
    opened.assert_not_called()
    assert key not in mod._verdict_inflight
    assert mod._recent_failure(key) is None


def test_claim_failure_is_reported_not_raised(monkeypatch):
    @contextmanager
    def _broken(key):
        raise RuntimeError("database unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(mod, "_pair_check_claim", _broken)
    key = ("acc", "a", "b")
    mod._verdict_inflight[key] = mod.Future()
    mod._run_verdict_check(key, "acc", "a", "b", None)
    assert mod._recent_failure(key) == "ai_model_error"
    assert key not in mod._verdict_inflight


def test_one_account_cannot_queue_past_its_cap(monkeypatch):
    submitted = []

    class _Executor:
        def submit(self, fn, *args):
            submitted.append(args[0])
            return mod.Future()

    monkeypatch.setattr(mod, "_get_verdict_executor", lambda: _Executor())
    monkeypatch.setattr(mod, "_VERDICT_MAX_INFLIGHT_PER_ACCOUNT", 3)
    for i in range(5):
        mod._schedule_verdict_check(("acc", f"a{i}", "b"), "acc", "a", "b", None)
    mod._schedule_verdict_check(("other", "a", "b"), "other", "a", "b", None)
    assert [k[0] for k in submitted] == ["acc", "acc", "acc", "other"]


def test_process_cap_bounds_the_queue(monkeypatch):
    submitted = []

    class _Executor:
        def submit(self, fn, *args):
            submitted.append(args[0])
            return mod.Future()

    monkeypatch.setattr(mod, "_get_verdict_executor", lambda: _Executor())
    monkeypatch.setattr(mod, "_VERDICT_MAX_INFLIGHT", 2)
    for i in range(4):
        mod._schedule_verdict_check((f"acc{i}", "a", "b"), f"acc{i}", "a", "b", None)
    assert len(submitted) == 2


def test_failure_map_is_swept_when_large(monkeypatch):
    monkeypatch.setattr(mod, "_VERDICT_FAILURES_MAX", 3)
    old = mod.time.monotonic() - 1000
    for i in range(3):
        mod._verdict_failures[("acc", str(i), "x")] = ("ai_model_error", old)
    mod._remember_failure(("acc", "new", "x"), "rate_limited")
    assert list(mod._verdict_failures) == [("acc", "new", "x")]


def test_advisory_claim_is_exclusive_across_connections(db_engine):
    """Two processes cannot check the same pair at once (real Postgres lock)."""
    key = ("acc", str(uuid4()), str(uuid4()))
    with mod._pair_check_claim(key) as first:
        assert first is True
        with mod._pair_check_claim(key) as second:
            assert second is False
    with mod._pair_check_claim(key) as again:
        assert again is True
