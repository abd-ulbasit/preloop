"""Managed Jira Cloud trackers through the trackers API (issue #1579)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from preloop.models.models.tracker import Tracker

GATEWAY = "https://api.atlassian.com"
SITE = "https://example-site.atlassian.net"
CLOUD_ID = "11223344-aaaa-bbbb-cccc-556677889900"
MANAGED_DETAILS = {
    "auth_type": "managed_oauth",
    "managed_oauth": True,
    "managed_state": "connected",
    "provider": "jira",
    "cloud_id": CLOUD_ID,
    "site_url": SITE,
    "site_name": "example-site",
    "actor": {"account_id": "acc-1", "name": "Jane Doe"},
}


@pytest.fixture(autouse=True)
def mock_event_bus_connect():
    with patch(
        "preloop.sync.services.event_bus.EventBus.connect", new_callable=AsyncMock
    ) as mock_connect:
        yield mock_connect


def _managed_jira(db_session, test_user) -> Tracker:
    tracker = Tracker(
        name="Jira (managed)",
        tracker_type="jira",
        url=GATEWAY,
        account_id=test_user.account_id,
        auth_type="managed_oauth",
        api_key=None,
        connection_details=dict(MANAGED_DETAILS),
    )
    db_session.add(tracker)
    db_session.commit()
    return tracker


@patch("preloop.api.endpoints.trackers.create_tracker_client")
def test_scope_preview_pins_site_and_uses_resolver(
    mock_create, client: TestClient, db_session, test_user
) -> None:
    tracker = _managed_jira(db_session, test_user)
    mock_client = AsyncMock()
    mock_client.test_connection.return_value.connected = True
    mock_client.get_organizations.return_value = [{"id": "site", "name": "site"}]
    mock_create.return_value = mock_client

    response = client.post(
        "/api/v1/trackers/test-and-list-orgs",
        json={
            "tracker_id": str(tracker.id),
            "tracker_type": "jira",
            "url": SITE,
            "api_key": "unchanged",
            # A request cannot repoint the grant at another site.
            "connection_details": {
                "cloud_id": "attacker-cloud-0000",
                "site_url": "https://evil.atlassian.net",
                "username": "x@y",
            },
        },
    )
    assert response.status_code == 200, response.text
    kwargs = mock_create.call_args.kwargs
    assert kwargs["api_key"] == ""
    details = kwargs["connection_details"]
    assert details["auth_type"] == "managed_oauth"
    assert details["cloud_id"] == CLOUD_ID
    assert details["site_url"] == SITE
    assert details["username"] == ""
    source = kwargs["credential_source"]
    assert source.provider == "jira" and source.repository is None


@patch("preloop.api.endpoints.trackers.event_bus_service.publish_task")
def test_update_keeps_site_binding_and_row_url(
    mock_publish, client: TestClient, db_session, test_user
) -> None:
    tracker = _managed_jira(db_session, test_user)
    response = client.put(
        f"/api/v1/trackers/{tracker.id}",
        json={
            "name": "Renamed",
            "url": SITE,
            "api_key": "unchanged",
            "connection_details": {"username": "x@y"},
        },
    )
    assert response.status_code == 200, response.text
    db_session.refresh(tracker)
    assert tracker.name == "Renamed"
    assert tracker.url == GATEWAY
    assert tracker.auth_type == "managed_oauth"
    assert tracker.api_key is None
    details = tracker.connection_details
    assert details["cloud_id"] == CLOUD_ID and details["site_url"] == SITE
    assert details["actor"]["name"] == "Jane Doe"
    assert "username" not in details


@patch("preloop.api.endpoints.trackers.event_bus_service.publish_task")
def test_update_refuses_site_change_and_pasted_token(
    mock_publish, client: TestClient, db_session, test_user
) -> None:
    tracker = _managed_jira(db_session, test_user)
    response = client.put(
        f"/api/v1/trackers/{tracker.id}",
        json={"connection_details": {"cloud_id": "other-cloud-0000"}},
    )
    assert response.status_code == 400
    response = client.put(
        f"/api/v1/trackers/{tracker.id}", json={"api_key": "ATATT-fresh"}
    )
    assert response.status_code == 409
    db_session.refresh(tracker)
    assert tracker.api_key is None


@patch("preloop.api.endpoints.trackers.event_bus_service.publish_task")
def test_editing_basic_auth_jira_never_converts_it(
    mock_publish, client: TestClient, db_session, test_user
) -> None:
    tracker = Tracker(
        name="Jira (token)",
        tracker_type="jira",
        url=SITE,
        account_id=test_user.account_id,
        auth_type="api_token",
        api_key="stored-key",
        connection_details={"username": "me@example.com"},
    )
    db_session.add(tracker)
    db_session.commit()
    response = client.put(
        f"/api/v1/trackers/{tracker.id}",
        json={"name": "Still token", "auth_type": "managed_oauth"},
    )
    db_session.refresh(tracker)
    assert tracker.auth_type == "api_token"
    assert tracker.connection_details["username"] == "me@example.com"
    assert response.status_code in (200, 400, 422)


def _jira_tree(db_session, tracker):
    from preloop.models.models.organization import Organization
    from preloop.models.models.project import Project
    from preloop.models.models.webhook import Webhook

    org = Organization(name="site", identifier="site", tracker_id=tracker.id)
    db_session.add(org)
    db_session.commit()
    projects = []
    for key, pid in (("PRJ", "10000"), ("OPS", "10001")):
        project = Project(name=key, identifier=pid, slug=key, organization_id=org.id)
        db_session.add(project)
        db_session.commit()
        db_session.add(
            Webhook(
                project_id=project.id,
                external_id="1000",
                url="https://preloop.example/hook",
                secret="",
                events=["jira:issue_updated"],
            )
        )
        db_session.commit()
        projects.append(project)
    return org, projects


def test_managed_webhook_ids_come_from_the_trackers_projects(db_session, test_user):
    from preloop.api.endpoints.webhooks import _managed_jira_webhook_ids

    tracker = _managed_jira(db_session, test_user)
    other = _managed_jira(db_session, test_user)
    _jira_tree(db_session, tracker)
    assert _managed_jira_webhook_ids(db_session, tracker) == {"1000"}
    assert _managed_jira_webhook_ids(db_session, other) == set()


@pytest.mark.asyncio
async def test_tracker_deletion_deletes_dynamic_webhooks(db_session, test_user):
    from preloop.models.models.webhook import Webhook
    from preloop.sync import tasks

    tracker = _managed_jira(db_session, test_user)
    _jira_tree(db_session, tracker)
    tracker.is_deleted = True
    db_session.commit()
    client = AsyncMock()
    with (
        patch.object(tasks, "get_db_session", lambda: iter([db_session])),
        patch(
            "preloop.sync.trackers.create_tracker_client",
            AsyncMock(return_value=client),
        ) as create,
        patch.object(db_session, "close"),
    ):
        await tasks.cleanup_tracker_webhooks(str(tracker.id))
    client.delete_dynamic_webhooks.assert_awaited_once_with(["1000"])
    client.delete_webhook.assert_not_called()
    assert create.await_args.kwargs["credential_source"].provider == "jira"
    assert db_session.query(Webhook).filter(Webhook.external_id == "1000").count() == 0
