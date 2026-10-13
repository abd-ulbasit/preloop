"""Native context admission, scoped receipts and authoritative lifecycle tests."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from preloop_hermes_plugin.notes import OperatorNotes  # noqa: E402
from preloop_hermes_plugin.plugin import HermesPreloopPlugin  # noqa: E402

TEXT = '<operator-notes><operator-note id="note-1">Synthetic steer.</operator-note></operator-notes>'


def fixture() -> tuple[OperatorNotes, list[tuple[str, dict[str, Any]]], list[bool]]:
    """Provide the API's real note_id shape and switchable ACK availability."""
    calls: list[tuple[str, dict[str, Any]]] = []
    fail = [False]

    def request(path: str, body: dict[str, Any]) -> dict[str, Any]:
        calls.append((path, body))
        if path == "session-start":
            return {"runtime_session_id": "runtime-synthetic"}
        if path == "notes/pending":
            return {"text": TEXT, "notes": [{"note_id": "note-1"}]}
        if path == "notes/ack" and fail[0]:
            raise OSError("offline")
        return {"ok": True}

    return OperatorNotes("https://example.com", "agt_synthetic", request), calls, fail


def test_claim_then_context_admission_then_ack() -> None:
    notes, calls, _ = fixture()
    assert notes.prepare("session-1", "turn-1") == {"context": TEXT}
    assert calls == [
        ("session-start", {"session_id": "session-1", "source": "hermes"}),
        (
            "notes/pending",
            {
                "session_id": "session-1",
                "channel": "hook",
                "event": "pre_tool_use",
                "defer_delivery": True,
            },
        ),
    ]
    notes.admitted("session-2", "turn-1", [{"content": TEXT}])
    notes.admitted("session-1", "turn-2", [{"content": TEXT}])
    notes.admitted("session-1", "turn-1", [{"content": "stripped"}])
    assert len(calls) == 2
    notes.admitted(
        "session-1", "turn-1", [{"content": [{"type": "text", "text": TEXT}]}]
    )
    assert calls[-1] == (
        "notes/ack",
        {"session_id": "session-1", "note_ids": ["note-1"]},
    )


def test_ack_failure_does_not_reinject_or_claim_more() -> None:
    notes, calls, fail = fixture()
    notes.prepare("session-1", "turn-1")
    fail[0] = True
    notes.admitted("session-1", "turn-1", [{"content": TEXT}])
    assert notes.prepare("session-1", "turn-2") is None
    assert sum(path == "notes/pending" for path, _ in calls) == 1
    fail[0] = False
    notes.prepare("session-1", "turn-2")
    assert sum(path == "notes/pending" for path, _ in calls) == 2


def test_unadmitted_batch_can_retry_only_after_own_turn_ends() -> None:
    notes, calls, _ = fixture()
    notes.prepare("session-1", "turn-1")
    assert notes.prepare("session-1", "turn-2") is None
    notes.turn_ended("session-1", "foreign")
    assert notes.prepare("session-1", "turn-2") is None
    notes.turn_ended("session-1", "turn-1")
    assert notes.prepare("session-1", "turn-2") == {"context": TEXT}
    assert len(calls) == 2
    notes.admitted("session-1", "turn-2", [{"content": TEXT}])
    assert calls[-1][0] == "notes/ack"


def test_finalize_uses_external_session_identity_not_per_turn_end() -> None:
    notes, calls, _ = fixture()
    notes.prepare("session-1", "turn-1")
    notes.turn_ended("session-1", "turn-1")
    assert not any(path == "session-end" for path, _ in calls)
    notes.finalized("foreign", "shutdown")
    assert len(calls) == 2
    notes.finalized("session-1", "shutdown")
    assert calls[-1] == (
        "session-end",
        {"external_session_id": "session-1", "reason": "shutdown"},
    )
    assert not notes.sessions


def test_concurrent_session_callbacks_claim_once() -> None:
    notes, calls, _ = fixture()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: notes.prepare("session-1", "turn-1"), range(8))
        )
    assert all(result == {"context": TEXT} for result in results)
    assert sum(path == "notes/pending" for path, _ in calls) == 1


def test_missing_identity_bad_registration_and_outage_never_claim() -> None:
    notes, calls, _ = fixture()
    assert notes.prepare("", "turn-1") is None
    assert notes.prepare("session-1", "") is None
    assert not calls
    requests: list[str] = []

    def invalid(path: str, body: dict[str, Any]) -> dict[str, Any]:
        requests.append(path)
        return {}

    notes.transport = invalid
    assert notes.prepare("session-1", "turn-1") is None
    assert requests == ["session-start"]

    def offline(path: str, body: dict[str, Any]) -> dict[str, Any]:
        raise OSError("offline")

    notes.transport = offline
    assert notes.prepare("session-1", "turn-1") is None


def test_host_registry_requires_paired_context_and_admission_hooks(
    monkeypatch: Any,
) -> None:
    module = ModuleType("hermes_cli.plugins")
    module.VALID_HOOKS = {
        "pre_llm_call",
        "pre_api_request",
        "on_session_end",
        "on_session_finalize",
    }
    monkeypatch.setitem(sys.modules, "hermes_cli", ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", module)
    hooks: dict[str, Any] = {}

    class Context:
        def register_hook(self, name: str, callback: Any) -> None:
            hooks[name] = callback

    HermesPreloopPlugin().register(Context())
    assert set(hooks) == {
        "pre_tool_call",
        "pre_llm_call",
        "pre_api_request",
        "on_session_end",
        "on_session_finalize",
    }
    hooks.clear()
    module.VALID_HOOKS = {"pre_llm_call"}
    HermesPreloopPlugin().register(Context())
    assert set(hooks) == {"pre_tool_call"}


def test_finalize_retries_confirmed_ack_before_terminalizing() -> None:
    notes, calls, fail = fixture()
    notes.prepare("session-1", "turn-1")
    fail[0] = True
    notes.admitted("session-1", "turn-1", [{"content": TEXT}])
    notes.finalized("session-1", "shutdown")
    assert not any(path == "session-end" for path, _ in calls)
    fail[0] = False
    notes.finalized("session-1", "shutdown")
    assert [path for path, _ in calls[-2:]] == ["notes/ack", "session-end"]


def test_registration_refusal_keeps_tool_approval_and_disables_partial_notes(
    monkeypatch: Any,
) -> None:
    module = ModuleType("hermes_cli.plugins")
    module.VALID_HOOKS = {"pre_llm_call", "pre_api_request"}
    monkeypatch.setitem(sys.modules, "hermes_cli", ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", module)
    hooks: dict[str, Any] = {}

    class Context:
        def register_hook(self, name: str, callback: Any) -> None:
            if name == "pre_api_request":
                raise ValueError("unsupported hook")
            hooks[name] = callback

    plugin = HermesPreloopPlugin()
    plugin.register(Context())
    assert callable(hooks["pre_tool_call"])
    assert hooks["pre_llm_call"](session_id="synthetic", turn_id="synthetic") is None
