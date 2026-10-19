"""Hostname fallback that places unlinked managed agents on fleet devices."""

from types import SimpleNamespace as Row
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from preloop.models import models
from preloop.services.agent_device_links import agent_device_links
from preloop.services.agent_device_resolution import (
    agent_kind_for_harness,
    normalize_hostname,
    resolve_hostname_devices,
)


@pytest.mark.parametrize(
    "raw",
    [
        "Dimitriss-MacBook-Pro.local",
        "dimitriss-macbook-pro",
        "dimitriss_macbook_pro",
        "Dimitriss MacBook Pro",
        "dimitriss-macbook-pro.corp.example.com",
    ],
)
def test_hostnames_normalize_alike(raw: str) -> None:
    assert normalize_hostname(raw) == "dimitriss-macbook-pro"


@pytest.mark.parametrize("raw", [None, "", "  ", ".local", 3])
def test_unusable_hostnames_normalize_to_none(raw: Any) -> None:
    assert normalize_hostname(raw) is None


def test_harness_ids_map_to_agent_kinds() -> None:
    assert agent_kind_for_harness("codex_cli") == "codex"
    assert agent_kind_for_harness("vscode_copilot") == "vscode"
    assert agent_kind_for_harness("claude_code") == "claude_code"


def _device(key: str, hostname: str | None, users: tuple[str, ...] = ()) -> dict:
    return dict(
        device_key=key,
        hostname=hostname,
        users=[dict(id=u) for u in users],
        runners=[],
        agents=[],
    )


def _agent(**changes: Any) -> Row:
    return Row(
        **(
            dict(
                id="agent-a",
                owner_user_id="owner-a",
                enrollment_hostname="dimitriss-macbook-pro",
            )
            | changes
        )
    )


def test_founder_laptop_example_matches() -> None:
    devices = [_device("fp:17013ee1dc40", "Dimitriss-MacBook-Pro.local", ("owner-a",))]
    assert resolve_hostname_devices(devices, [_agent()]) == {
        "agent-a": "fp:17013ee1dc40"
    }


def test_ambiguous_hostname_links_nothing() -> None:
    devices = [
        _device("fp:one", "Dimitriss-MacBook-Pro.local"),
        _device("fp:two", "dimitriss-macbook-pro"),
    ]
    assert resolve_hostname_devices(devices, [_agent()]) == {}


def test_owner_mismatch_links_nothing() -> None:
    devices = [_device("fp:one", "Dimitriss-MacBook-Pro.local", ("someone-else",))]
    assert resolve_hostname_devices(devices, [_agent()]) == {}


def test_unknown_owner_or_users_still_link() -> None:
    devices = [_device("fp:one", "Dimitriss-MacBook-Pro.local")]
    assert resolve_hostname_devices(devices, [_agent()]) == {"agent-a": "fp:one"}
    devices = [_device("fp:one", "Dimitriss-MacBook-Pro.local", ("owner-a",))]
    assert resolve_hostname_devices(devices, [_agent(owner_user_id=None)]) == {
        "agent-a": "fp:one"
    }


def test_already_linked_agent_is_left_alone() -> None:
    linked = _device("fp:other", "other")
    linked["agents"] = [dict(managed_agent_id="agent-a")]
    devices = [_device("fp:one", "Dimitriss-MacBook-Pro.local"), linked]
    assert resolve_hostname_devices(devices, [_agent()]) == {}


def test_agent_page_uses_the_same_resolution() -> None:
    """The agent's Device strip names the device the fleet map places it on."""
    user = models.User(id=uuid4(), account_id=uuid4(), is_superuser=True)
    service = MagicMock()
    service.devices_for_agent.return_value = []
    device = _device("fp:laptop", "Dimitriss-MacBook-Pro.local", ("owner-a",))
    device |= dict(os="darwin", runner_state="online", last_seen_at=None)
    service.read.return_value = dict(truncated=False, items=[device])
    with (
        patch("preloop.services.agent_device_links.get_plugin_manager") as manager,
        patch("preloop.services.agent_device_links.crud") as crud,
        patch("preloop.services.fleet_map._allowed", return_value=True),
    ):
        manager.return_value.get_service.return_value = service
        crud.crud_managed_agent.get_for_account.return_value = _agent()
        links = agent_device_links(MagicMock(), user=user, agent_id="agent-a")
        assert [link.device_key for link in links] == ["fp:laptop"]
        service.read.return_value = dict(truncated=True, items=[device])
        assert agent_device_links(MagicMock(), user=user, agent_id="agent-a") == []
    with (
        patch("preloop.services.agent_device_links.get_plugin_manager") as manager,
        patch("preloop.services.agent_device_links.crud") as crud,
        patch("preloop.services.fleet_map._allowed", return_value=False),
    ):
        manager.return_value.get_service.return_value = service
        service.read.return_value = dict(truncated=False, items=[device])
        crud.crud_managed_agent.get_for_account.return_value = _agent()
        assert agent_device_links(MagicMock(), user=user, agent_id="agent-a") == []


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("Codex CLI", "codex"),
        ("GitHub Copilot CLI", "copilot_cli"),
        ("VS Code Copilot", "vscode"),
        ("Cursor CLI", "cursor"),
        ("devin_cli", "devin"),
        ("Claude Desktop", "claude_desktop"),
    ],
)
def test_display_names_map_to_agent_kinds(raw: str, kind: str) -> None:
    assert agent_kind_for_harness(raw) == kind


def _inventory_device(*kinds: str, hostname: str | None = "Laptop.local") -> dict:
    device = _device("fp:laptop", hostname, ("owner-a",))
    device["agents"] = [
        dict(id=f"runner-1:{k}", kind=k, status="pending", managed_agent_id=None)
        for k in kinds
    ]
    return device


def test_inventory_entry_places_the_one_managed_agent_of_its_kind() -> None:
    cursor = _agent(id="agent-cursor", agent_kind="cursor", enrollment_hostname=None)
    other = _agent(id="agent-codex", agent_kind="codex", enrollment_hostname=None)
    devices = [_inventory_device("cursor_cli")]
    assert resolve_hostname_devices(devices, [cursor, other]) == {
        "agent-cursor": "fp:laptop"
    }


def test_inventory_match_requires_the_same_owner() -> None:
    cursor = _agent(
        id="agent-cursor",
        agent_kind="cursor",
        owner_user_id="x",
        enrollment_hostname=None,
    )
    assert resolve_hostname_devices([_inventory_device("cursor_cli")], [cursor]) == {}


def test_inventory_match_rejects_an_inconsistent_enrollment_hostname() -> None:
    cursor = _agent(id="agent-cursor", agent_kind="cursor", enrollment_hostname="desk")
    assert resolve_hostname_devices([_inventory_device("cursor_cli")], [cursor]) == {}


def test_two_plausible_agents_of_one_kind_are_not_folded() -> None:
    one = _agent(id="agent-1", agent_kind="cursor", enrollment_hostname="laptop")
    two = _agent(id="agent-2", agent_kind="cursor", enrollment_hostname=None)
    assert resolve_hostname_devices([_inventory_device("cursor_cli")], [one, two]) == {}
