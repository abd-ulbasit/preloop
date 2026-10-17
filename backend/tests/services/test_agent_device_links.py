"""The optional fleet extension must not affect standalone agent reads."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from preloop.models import models
from preloop.services.agent_device_links import agent_device_links


@pytest.mark.parametrize("service", [None, SimpleNamespace()])
def test_missing_or_older_fleet_plugin_returns_no_devices(service: object) -> None:
    with patch("preloop.services.agent_device_links.get_plugin_manager") as manager:
        manager.return_value.get_service.return_value = service
        assert agent_device_links(MagicMock(), user=models.User(), agent_id="a") == []


def test_no_user_does_not_consult_plugin() -> None:
    with patch("preloop.services.agent_device_links.get_plugin_manager") as manager:
        assert agent_device_links(MagicMock(), user=None, agent_id="a") == []
        manager.assert_not_called()


def test_provider_receives_authenticated_user_and_returns_only_public_fields() -> None:
    db = MagicMock()
    user = models.User(id=uuid4(), account_id=uuid4())
    with patch("preloop.services.agent_device_links.get_plugin_manager") as manager:
        provider = manager.return_value.get_service.return_value.devices_for_agent
        provider.return_value = [
            {
                "device_key": "fp:" + "a" * 64,
                "hostname": "workstation",
                "os": "linux",
                "runner_state": "online",
                "secret": "must not leave the service",
            }
        ]
        result = agent_device_links(db, user=user, agent_id="agent-id")
        provider.assert_called_once_with(db, user=user, agent_id="agent-id")
        assert result[0].hostname == "workstation"
        assert "secret" not in result[0].model_dump()


def test_provider_failure_is_logged_without_failing_agent_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch("preloop.services.agent_device_links.get_plugin_manager") as manager:
        provider = manager.return_value.get_service.return_value.devices_for_agent
        provider.side_effect = RuntimeError("Synthetic projection failure")
        assert (
            agent_device_links(
                MagicMock(), user=models.User(), agent_id="example-agent"
            )
            == []
        )
    assert "Failed to load optional device links for agent example-agent" in caplog.text


def test_incompatible_projection_response_is_also_isolated() -> None:
    with patch("preloop.services.agent_device_links.get_plugin_manager") as manager:
        provider = manager.return_value.get_service.return_value.devices_for_agent
        provider.return_value = [{"device_key": "fp:" + "a" * 64}]
        assert (
            agent_device_links(
                MagicMock(), user=models.User(), agent_id="example-agent"
            )
            == []
        )
