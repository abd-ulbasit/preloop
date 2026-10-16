"""Safe internal dispatch extension events never contain call payloads."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from preloop.services.mcp_observation import (
    MCPDispatchObservation,
    publish_dispatch_observation,
)


def test_publisher_passes_only_validated_identity() -> None:
    observer = MagicMock()
    account, server, session = (str(uuid4()) for _ in range(3))
    with patch(
        "preloop.plugins.base.get_plugin_manager",
        return_value=SimpleNamespace(get_service=lambda name: observer),
    ):
        publish_dispatch_observation(
            account_id=account,
            server_id=server,
            registry_revision="2026-10-16T12:00:00",
            runtime_session_id=session,
        )
    event = observer.observe.call_args.args[0]
    assert set(event.model_dump()) == {
        "account_id",
        "server_id",
        "registry_revision",
        "observed_at",
        "runtime_session_id",
    }
    assert str(event.account_id) == account and str(event.server_id) == server


def test_missing_revision_and_no_plugin_emit_nothing() -> None:
    observer = MagicMock()
    with patch(
        "preloop.plugins.base.get_plugin_manager",
        return_value=SimpleNamespace(get_service=lambda name: observer),
    ):
        publish_dispatch_observation(
            account_id=str(uuid4()),
            server_id=str(uuid4()),
            registry_revision=None,
            runtime_session_id=None,
        )
    observer.observe.assert_not_called()
    with patch(
        "preloop.plugins.base.get_plugin_manager",
        return_value=SimpleNamespace(get_service=lambda name: None),
    ):
        publish_dispatch_observation(
            account_id=str(uuid4()),
            server_id=str(uuid4()),
            registry_revision="2026-10-16T12:00:00",
            runtime_session_id=None,
        )


def test_observer_failure_does_not_change_execution_outcome() -> None:
    observer = MagicMock()
    observer.observe.side_effect = RuntimeError("synthetic failure")
    with patch(
        "preloop.plugins.base.get_plugin_manager",
        return_value=SimpleNamespace(get_service=lambda name: observer),
    ):
        publish_dispatch_observation(
            account_id=str(uuid4()),
            server_id=str(uuid4()),
            registry_revision="2026-10-16T12:00:00",
            runtime_session_id=None,
        )
    observer.observe.assert_called_once()


@pytest.mark.parametrize(
    "field",
    ["url", "arguments", "headers", "prompt", "auth", "tool_name", "server_name"],
)
def test_contract_refuses_content_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        MCPDispatchObservation.model_validate(
            {
                "account_id": uuid4(),
                "server_id": uuid4(),
                "registry_revision": "2026-10-16T12:00:00",
                "observed_at": datetime.now(UTC),
                field: "synthetic-content",
            }
        )


def test_revision_cannot_be_a_label_or_endpoint() -> None:
    with pytest.raises(ValidationError):
        MCPDispatchObservation(
            account_id=uuid4(),
            server_id=uuid4(),
            registry_revision="http://example.com",
            observed_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_dispatch_uses_snapshot_identity_and_works_without_a_session() -> None:
    from preloop.services.dynamic_fastmcp import DynamicFastMCP, GrantDispatchSnapshot
    from preloop.services.dynamic_mcp_server import UserContext

    account_id, server_id = str(uuid4()), str(uuid4())
    snapshot = GrantDispatchSnapshot(
        account_id=account_id,
        tool_name="read",
        server_name="same-label",
        upstream_name="read",
        registry_revision="2026-10-16T12:00:00",
        client_config={
            "server_id": server_id,
            "url": "http://example.com/mcp",
            "auth_config": {"token": "synthetic-secret"},
        },
    )
    context = UserContext(
        user_id=str(uuid4()), account_id=account_id, username="synthetic"
    )
    gateway = DynamicFastMCP("synthetic")
    gateway._proxied_tool_servers["read"] = str(uuid4())
    observer = MagicMock()
    with patch(
        "preloop.plugins.base.get_plugin_manager",
        return_value=SimpleNamespace(get_service=lambda name: observer),
    ):
        await gateway._observe_mcp_dispatch(snapshot, context)
    event = observer.observe.call_args.args[0]
    assert str(event.server_id) == server_id
    assert event.runtime_session_id is None
    assert "synthetic-secret" not in repr(event)
    assert "example.com" not in repr(event)
