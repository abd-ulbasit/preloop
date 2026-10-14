"""Pasted Jira tokens: cloud id persistence and manual webhook instructions.

These tests call the endpoint helpers directly. They do not need a migrated
database, and they still cover the values the tracker page renders.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from preloop.api.endpoints.trackers import (
    _jira_cloud_id_from_client,
    _jira_manual_webhook,
    _merge_jira_token_details,
)

CLOUD_ID = "11223344-aaaa-bbbb-cccc-556677889900"
TOKEN = "scoped-token-value"


def test_cloud_id_from_client_accepts_only_a_string():
    assert _jira_cloud_id_from_client(SimpleNamespace(cloud_id=CLOUD_ID)) == CLOUD_ID
    assert _jira_cloud_id_from_client(SimpleNamespace(cloud_id="  ")) is None
    assert _jira_cloud_id_from_client(SimpleNamespace()) is None
    # An async test double grows attributes on access. Those must not be stored.
    assert _jira_cloud_id_from_client(AsyncMock()) is None
    assert _jira_cloud_id_from_client(MagicMock()) is None


def test_username_edit_keeps_scoped_binding_and_a_new_site_drops_it():
    previous = {
        "username": "ada@example.com",
        "cloud_id": CLOUD_ID,
        "webhook_status": "manual_required",
        "manual_webhook": {"organization_id": "org-1"},
        "api_key": TOKEN,
    }
    kept = _merge_jira_token_details(
        previous,
        {"username": "edited@example.com"},
        reset_binding=False,
    )
    assert kept["username"] == "edited@example.com"
    assert kept["cloud_id"] == CLOUD_ID
    assert kept["webhook_status"] == "manual_required"
    assert kept["manual_webhook"]["organization_id"] == "org-1"
    assert "api_key" not in kept
    assert TOKEN not in str(kept)

    reset = _merge_jira_token_details(
        previous,
        {"username": "edited@example.com"},
        reset_binding=True,
    )
    assert reset == {"username": "edited@example.com"}


def test_manual_webhook_uses_the_organization_id_and_secret(monkeypatch):
    monkeypatch.setenv("PRELOOP_URL", "https://preloop.example.com")
    org_id = uuid4()
    org = SimpleNamespace(id=org_id, webhook_secret="org-webhook-secret")
    tracker = SimpleNamespace(
        id=uuid4(),
        tracker_type="jira",
        connection_details={
            "username": "ada@example.com",
            "cloud_id": CLOUD_ID,
            "webhook_status": "manual_required",
            "manual_webhook": {
                "organization_id": str(org_id),
                "events": [
                    "jira:issue_created",
                    "jira:issue_updated",
                    "comment_created",
                    "comment_updated",
                ],
            },
        },
    )
    with patch("preloop.api.endpoints.trackers.crud_organization") as crud:
        crud.get_for_tracker.return_value = [org]
        manual = _jira_manual_webhook(MagicMock(), tracker)

    assert manual is not None
    assert manual.organization_id == str(org_id)
    assert (
        manual.url
        == f"https://preloop.example.com/api/v1/private/webhooks/jira/{org_id}"
    )
    assert manual.secret == "org-webhook-secret"
    assert "comment_created" in manual.events
    assert "comment_updated" in manual.events
    assert TOKEN not in manual.url
    assert TOKEN not in manual.secret


def test_manual_webhook_is_absent_unless_registration_is_impossible():
    tracker = SimpleNamespace(
        id=uuid4(),
        tracker_type="jira",
        connection_details={"username": "ada@example.com", "cloud_id": CLOUD_ID},
    )
    assert _jira_manual_webhook(MagicMock(), tracker) is None
