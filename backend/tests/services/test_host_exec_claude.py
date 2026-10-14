"""Claude Code runner host execution (#1668): maps, routing and completion."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from preloop.agents.factory import create_executor_for_execution
from preloop.agents.remote_runner import RemoteRunnerExecutor
from preloop.models.schemas.flow_runner import HarnessInventory
from preloop.services.host_exec import (
    HOST_EXEC_AGENT_TYPE_BY_HARNESS,
    finalize_runner_completion,
    harness_selector_config_error,
    host_exec_effective_profile,
    host_exec_flow_error,
    host_exec_harness,
    host_exec_harness_selector,
    host_exec_model_identifier,
    is_host_exec_agent_type,
    normalize_host_exec_advertisements,
    runner_has_host_exec_profile,
)
from preloop.services.runner_service import (
    harness_options,
    runner_eligible_for_harness,
)

SESSION_ID = "5e0c9a8e-2f7b-4d43-9a51-0d4f1c2b3a4e"


@pytest.fixture(autouse=True)
def runtime_admission_allowed(monkeypatch):
    monkeypatch.setattr(
        "preloop.models.crud.crud_flow_execution.admit_runtime_start",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "preloop.agents.remote_runner.crud_flow_execution.get_stop_request",
        lambda *args, **kwargs: None,
    )


def _claude_entry(**changes: Any) -> Dict[str, Any]:
    """An inventory entry shaped like the runner's verified Claude Code."""
    entry: Dict[str, Any] = {
        "harness": "claude_code",
        "display_name": "Claude Code",
        "version": "2.1.296",
        "login_state": "signed_in",
        "login_source": "cli_status",
        "account_host": None,
        "governance": "governed",
        "support_level": "flows_only",
        "enabled": True,
        "sessions_enabled": False,
        "session_mode": "none",
        "billing": "seat",
        "models": [
            {"id": "opus", "source": "static"},
            {"id": "sonnet", "source": "static"},
            {"id": "haiku", "source": "static"},
        ],
        "generated_profile": "claude",
        "capabilities": ["host_exec", "claude_code", "stdout", "cancel"],
    }
    entry.update(changes)
    return entry


def _inventory(**changes: Any) -> Dict[str, Any]:
    inventory = {
        "schema": 1,
        "generated_at": "2026-10-14T09:00:00Z",
        "hash": "sha256:" + "0" * 64,
        "entries": [_claude_entry(**changes)],
    }
    HarnessInventory.model_validate(inventory)
    return inventory


def _runner(
    account_id: Any,
    *,
    inventory: Optional[Dict[str, Any]] = None,
    name: str = "mac",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        account_id=account_id,
        name=name,
        labels=[],
        registered_by_user_id=uuid4(),
        status="online",
        last_heartbeat=datetime.now(timezone.utc),
        free_slots=1,
        harness_inventory=_inventory() if inventory is None else inventory,
        capabilities=normalize_host_exec_advertisements(
            [
                {
                    "name": "claude",
                    "capabilities": ["host_exec", "claude_code", "stdout", "cancel"],
                    "models": ["haiku", "opus", "sonnet"],
                }
            ]
        ),
    )


def test_claude_code_is_a_host_exec_agent_type() -> None:
    assert is_host_exec_agent_type("claude_code")
    assert host_exec_harness("claude_code") == "claude_code"
    assert HOST_EXEC_AGENT_TYPE_BY_HARNESS["claude_code"] == "claude_code"
    assert (
        host_exec_model_identifier("claude_code", {"claude_code_model": " opus "})
        == "opus"
    )
    assert host_exec_model_identifier("claude_code", {"copilot_model": "x"}) is None


def test_claude_advertisement_survives_registration() -> None:
    runner = _runner(uuid4())
    caps = runner.capabilities["host_exec_profiles"][0]["capabilities"]
    assert "claude_code" in caps
    assert runner_has_host_exec_profile(
        runner, "claude", "opus", agent_type="claude_code"
    )
    assert not runner_has_host_exec_profile(runner, "claude", agent_type="copilot")


def test_harness_selector_and_flow_validation() -> None:
    config = {"harness": "claude_code", "claude_code_model": "opus"}
    assert host_exec_harness_selector("claude_code", config) == "claude_code"
    assert host_exec_effective_profile("claude_code", config) == "claude"
    assert harness_selector_config_error("claude_code", config) is None
    assert "requires agent_type claude_code" in (
        harness_selector_config_error("copilot", {"harness": "claude_code"}) or ""
    )
    assert host_exec_flow_error(agent_type="claude_code", agent_config=config) is None
    assert "requires agent_config.host_exec_profile" in (
        host_exec_flow_error(agent_type="claude_code", agent_config={}) or ""
    )
    assert "hosted compute" in (
        host_exec_flow_error(
            agent_type="claude_code", agent_config=config, runner_pool="server"
        )
        or ""
    )


def test_verified_claude_runner_is_eligible_and_presence_only_is_not() -> None:
    account_id = uuid4()
    pinned = _runner(account_id)
    ok, reason, _ = runner_eligible_for_harness(
        None,
        pinned,
        account_id=account_id,
        harness="claude_code",
        model="opus",
        pinned_runner_id=pinned.id,
    )
    assert (ok, reason) == (True, None)
    # Older runners and unverified logins report presence only.
    old = _runner(
        account_id,
        inventory=_inventory(
            support_level="presence_only", generated_profile=None, capabilities=[]
        ),
    )
    ok, reason, _ = runner_eligible_for_harness(
        None,
        old,
        account_id=account_id,
        harness="claude_code",
        pinned_runner_id=old.id,
    )
    assert (ok, reason) == (False, "no_runner_with_harness")


def test_harness_options_lists_claude_code() -> None:
    account_id = uuid4()
    result = harness_options(
        None, [_runner(account_id)], account_id=account_id, usable=lambda r: True
    )
    assert [item["harness"] for item in result["harnesses"]] == ["claude_code"]
    option = result["harnesses"][0]
    assert option["display_name"] == "Claude Code"
    assert option["agent_type"] == "claude_code"
    assert {m["id"] for m in option["models"]} == {"opus", "sonnet", "haiku"}


def _executor(config: Dict[str, Any]) -> RemoteRunnerExecutor:
    flow = SimpleNamespace(
        agent_type="claude_code",
        agent_config=config,
        runner_pool=None,
        git_clone_config=None,
        custom_commands=None,
        timeout_seconds=None,
        ai_model=None,
        account_id=uuid4(),
    )
    return RemoteRunnerExecutor(
        "claude_code",
        config,
        db=MagicMock(),
        pool="auto",
        account_id=uuid4(),
        flow=flow,
    )


def test_lease_payload_names_the_generated_claude_profile() -> None:
    executor = _executor({"harness": "claude_code", "claude_code_model": "opus"})
    payload = executor._lease_payload(
        execution_id=uuid4(), flow_id=uuid4(), prompt="tidy the memory"
    )
    assert payload["host_exec"] == {
        "harness": "claude_code",
        "profile": "claude",
        "model": "opus",
    }
    assert payload["host_exec_profile"] == "claude"
    assert payload["agent_type"] == "claude_code"
    assert payload["completion_protocol"] == "host_exec"
    dumped = json.dumps(payload)
    for forbidden in ("argv", "executable", "account_api_token", "image"):
        assert forbidden not in dumped


def test_factory_routes_claude_harness_flow_to_a_runner() -> None:
    flow = SimpleNamespace(
        runner_pool=None,
        account_id=uuid4(),
        agent_config={"harness": "claude_code"},
        account=SimpleNamespace(default_runner_pool=None),
    )
    executor = create_executor_for_execution(
        "claude_code",
        {"harness": "claude_code"},
        flow=flow,
        db=MagicMock(),
        execution_context={"agent_config": {"host_exec_profile": "claude"}},
    )
    assert isinstance(executor, RemoteRunnerExecutor)
    assert executor.pool == "auto"


def test_factory_refuses_claude_code_on_hosted_compute() -> None:
    with pytest.raises(ValueError, match="hosted compute"):
        create_executor_for_execution("claude_code", {})


def test_claude_completion_requires_the_claude_harness() -> None:
    pending = {
        "agent_type": "claude_code",
        "host_exec_profile": "claude",
        "completion_protocol": "host_exec",
    }
    message = {
        "status": "SUCCEEDED",
        "host_exec_profile": "claude",
        "completion_protocol": "host_exec",
        "exit_code": 0,
        "result": {
            "status": "success",
            "harness": "claude_code",
            "session_id": SESSION_ID,
            "model": "claude-opus-5-5",
        },
    }
    status, error, result = finalize_runner_completion(message, pending)
    assert status == "SUCCEEDED", error
    assert result is not None and result["gateway_metered"] is False
    message["result"]["harness"] = "copilot_cli"
    status, error, _ = finalize_runner_completion(message, pending)
    assert status == "FAILED"
    assert "Claude Code" in (error or "")
