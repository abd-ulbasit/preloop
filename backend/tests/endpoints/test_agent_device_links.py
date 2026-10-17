"""Optional fleet faults leave the primary agent detail read available."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from preloop.models import crud, models


def test_agent_header_survives_a_failing_fleet_provider(
    client: TestClient, db_session: Session, test_user: models.User
) -> None:
    agent = crud.crud_managed_agent.create_custom_agent(
        db_session,
        account_id=test_user.account_id,
        display_name="Example agent",
        owner_user_id=test_user.id,
        agent_kind="codex",
    )
    with patch("preloop.services.agent_device_links.get_plugin_manager") as manager:
        provider = manager.return_value.get_service.return_value.devices_for_agent
        provider.side_effect = RuntimeError("Synthetic projection failure")
        response = client.get(f"/api/v1/agents/{agent.id}", params={"include": ""})
    assert response.status_code == 200, response.text
    assert response.json()["agent"]["id"] == str(agent.id)
    assert response.json()["devices"] == []
