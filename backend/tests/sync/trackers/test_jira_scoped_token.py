"""Scoped Atlassian API tokens on the pasted-token Jira path.

A scoped token is rejected by ``https://<site>.atlassian.net`` and accepted
only by ``https://api.atlassian.com/ex/jira/<cloudId>``. Links stay on the
site. A classic token that already succeeds on the site is not moved.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from jira import JIRAError

from preloop.api.endpoints.trackers import _jira_manual_webhook
from preloop.models import models
from preloop.sync.exceptions import TrackerAuthenticationError, TrackerResponseError
from preloop.sync.scanner.core import TrackerClient, _process_organization
from preloop.sync.trackers.factory import create_tracker_client
from preloop.sync.trackers.jira import (
    WEBHOOK_STATUS_MANUAL_REQUIRED,
    JiraTracker,
)

SITE = "https://example-site.atlassian.net"
CLOUD_ID = "11223344-aaaa-bbbb-cccc-556677889900"
GATEWAY = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}"
TOKEN = "scoped-token-value"
EMAIL = "ada@example.com"


def _tracker(**details: object) -> JiraTracker:
    connection = {"url": SITE, "username": EMAIL, **details}
    return JiraTracker(
        "tracker-1",
        TOKEN,
        connection,
        initialize_client=False,
    )


def _mount(tracker: JiraTracker, handler) -> List[httpx.Request]:
    seen: List[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    tracker._transport = httpx.MockTransport(wrapped)
    return seen


def _auth_values(request: httpx.Request) -> str:
    return request.headers.get("authorization", "")


@pytest.mark.asyncio
async def test_scoped_token_falls_back_to_gateway_and_keeps_site_links():
    """401 on the site, cloudId from tenant_info, 200 on the gateway."""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "example-site.atlassian.net" and path == "/rest/api/2/myself":
            return httpx.Response(401, json={"message": "client must be authenticated"})
        if host == "example-site.atlassian.net" and path == "/_edge/tenant_info":
            return httpx.Response(200, json={"cloudId": CLOUD_ID})
        if (
            host == "api.atlassian.com"
            and path == f"/ex/jira/{CLOUD_ID}/rest/api/2/myself"
        ):
            return httpx.Response(200, json={"accountId": "abc", "displayName": "Ada"})
        if (
            host == "api.atlassian.com"
            and path == f"/ex/jira/{CLOUD_ID}/rest/api/2/project"
        ):
            return httpx.Response(
                200, json=[{"id": "10000", "key": "PRJ", "name": "Project"}]
            )
        return httpx.Response(599, json={"host": host, "path": path})

    tracker = _tracker()
    seen = _mount(tracker, handler)

    result = await tracker.test_connection()

    assert result.connected is True
    assert tracker.cloud_id == CLOUD_ID
    assert tracker.jira_url == SITE
    assert tracker.base_url == SITE
    assert tracker.api_root == GATEWAY
    assert tracker.connection_details["cloud_id"] == CLOUD_ID
    assert TOKEN not in str(tracker.connection_details)

    tenant = next(
        request for request in seen if request.url.path == "/_edge/tenant_info"
    )
    assert "authorization" not in {key.lower() for key in tenant.headers}
    gateway_calls = [
        request for request in seen if request.url.host == "api.atlassian.com"
    ]
    assert gateway_calls
    assert all(_auth_values(request).startswith("Basic ") for request in gateway_calls)
    assert all(TOKEN not in str(request.url) for request in seen)
    assert all(str(request.url).startswith(GATEWAY + "/") for request in gateway_calls)

    projects = await tracker.get_projects("example-site.atlassian.net")
    assert projects[0]["url"] == f"{SITE}/projects/PRJ"
    assert all(request.url.host == "api.atlassian.com" for request in seen[-1:])

    orgs = await tracker.get_organizations()
    assert orgs[0]["name"] == "example-site.atlassian.net"
    assert orgs[0]["url"] == SITE

    row = tracker.transform_issue(
        {
            "id": "9",
            "key": "PRJ-4",
            "fields": {"summary": "Cost", "status": {"name": "Open"}},
        },
        SimpleNamespace(id="project-1"),
    )
    assert row["external_url"] == f"{SITE}/browse/PRJ-4"
    assert row["meta_data"]["url"] == f"{SITE}/browse/PRJ-4"
    assert "api.atlassian.com" not in row["external_url"]


@pytest.mark.asyncio
async def test_classic_token_does_not_fall_back_to_the_gateway():
    """A 200 from the site is the classic path: no tenant_info, no gateway."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/2/myself":
            return httpx.Response(200, json={"accountId": "classic"})
        return httpx.Response(500, json={"unexpected": str(request.url)})

    tracker = _tracker()
    seen = _mount(tracker, handler)

    result = await tracker.test_connection()

    assert result.connected is True
    assert tracker.cloud_id is None
    assert tracker.api_root == SITE
    assert [request.url.path for request in seen] == ["/rest/api/2/myself"]
    assert seen[0].url.host == "example-site.atlassian.net"


@pytest.mark.asyncio
async def test_stored_cloud_id_uses_the_gateway_without_a_site_probe():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.atlassian.com":
            return httpx.Response(200, json={"accountId": "abc"})
        return httpx.Response(500, json={"unexpected": str(request.url)})

    tracker = _tracker(cloud_id=CLOUD_ID)
    seen = _mount(tracker, handler)

    result = await tracker.test_connection()

    assert result.connected is True
    assert tracker.jira_url == SITE
    assert [str(request.url) for request in seen] == [f"{GATEWAY}/rest/api/2/myself"]


@pytest.mark.asyncio
async def test_gateway_redirect_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/2/myself":
            return httpx.Response(401)
        if request.url.path == "/_edge/tenant_info":
            return httpx.Response(200, json={"cloudId": CLOUD_ID})
        if request.url.host == "api.atlassian.com":
            return httpx.Response(
                302, headers={"location": "https://evil.example/steal"}
            )
        return httpx.Response(500)

    tracker = _tracker()
    seen = _mount(tracker, handler)
    result = await tracker.test_connection()

    assert result.connected is False
    assert "redirect" in result.message
    gateway = [request for request in seen if request.url.host == "api.atlassian.com"]
    assert len(gateway) == 1
    assert TOKEN not in str(gateway[0].url)


@pytest.mark.asyncio
async def test_non_cloud_host_does_not_probe_tenant_info():
    tracker = JiraTracker(
        "tracker-1",
        TOKEN,
        {"url": "https://jira.example.com", "username": EMAIL},
        initialize_client=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    seen = _mount(tracker, handler)
    with pytest.raises(TrackerAuthenticationError):
        await tracker._make_request("GET", "myself")
    assert [request.url.path for request in seen] == ["/rest/api/2/myself"]


def test_scoped_webhook_failure_records_manual_status_without_raising():
    """401 and 403 on both webhook endpoints record manual_required."""
    tracker = _tracker(cloud_id=CLOUD_ID)
    tracker.jira_client = MagicMock()
    classic = MagicMock()
    classic.status_code = 401
    classic.text = "unauthorized"
    tracker.jira_client._session.post.return_value = classic

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rest/webhooks/1.0/webhook"):
            return httpx.Response(401, json={"message": "unauthorized"})
        if request.url.path.endswith("/rest/api/3/webhook"):
            return httpx.Response(
                403,
                json={
                    "message": "Only Connect and OAuth 2.0 apps can use this operation"
                },
            )
        return httpx.Response(500, json={"path": request.url.path})

    seen = _mount(tracker, handler)
    org_id = "22222222-2222-2222-2222-222222222222"
    webhook_url = f"https://preloop.example.com/api/v1/private/webhooks/jira/{org_id}"
    row = MagicMock()
    row.connection_details = {"username": EMAIL, "cloud_id": CLOUD_ID}
    project = SimpleNamespace(id="project-1", identifier="PRJ")

    with (
        patch("preloop.sync.trackers.jira.crud_tracker") as crud,
        patch("preloop.sync.trackers.jira.crud_webhook") as crud_webhook,
    ):
        crud.get.return_value = row
        crud_webhook.get_by_project_id.return_value = None
        result = tracker.register_webhook(
            db=MagicMock(),
            project=project,
            webhook_url=webhook_url,
            secret="org-webhook-secret",
        )

    assert result is False
    assert tracker.webhook_status == WEBHOOK_STATUS_MANUAL_REQUIRED
    stored = crud.update.call_args.kwargs["obj_in"]["connection_details"]
    assert stored["webhook_status"] == WEBHOOK_STATUS_MANUAL_REQUIRED
    manual = stored["manual_webhook"]
    assert manual["organization_id"] == org_id
    assert manual["url"] == webhook_url
    assert "jira:issue_created" in manual["events"]
    assert "jira:issue_updated" in manual["events"]
    assert "comment_created" in manual["events"]
    assert "comment_updated" in manual["events"]
    assert TOKEN not in manual["url"]
    assert "org-webhook-secret" not in str(stored)
    assert any(request.url.path.endswith("/rest/api/3/webhook") for request in seen)
    assert any(request.url.host == "api.atlassian.com" for request in seen)


def test_classic_webhook_still_auto_registers():
    tracker = _tracker()
    tracker.jira_client = MagicMock()
    created = MagicMock()
    created.status_code = 201
    created.text = ""
    created.json.return_value = {"id": "55"}
    created.raise_for_status.return_value = None
    verify = MagicMock()
    verify.status_code = 200
    tracker.jira_client._session.post.return_value = created
    tracker.jira_client._session.get.return_value = verify
    project = SimpleNamespace(id="project-1", identifier="PRJ")

    with (
        patch("preloop.sync.trackers.jira.crud_webhook") as crud_webhook,
        patch("preloop.sync.trackers.jira.crud_tracker") as crud_tracker,
    ):
        crud_webhook.get_by_project_id.return_value = None
        result = tracker.register_webhook(
            db=MagicMock(),
            project=project,
            webhook_url="https://preloop.example.com/api/v1/private/webhooks/jira/org-1",
            secret="org-webhook-secret",
        )

    assert result is True
    crud_webhook.create.assert_called_once()
    crud_tracker.update.assert_not_called()
    tracker.jira_client._session.post.assert_called_once()
    assert tracker.webhook_status != WEBHOOK_STATUS_MANUAL_REQUIRED


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize("registration_raises", [False, True])
async def test_scoped_webhook_lookup_denied_still_exposes_manual_setup(
    monkeypatch: pytest.MonkeyPatch, status_code: int, registration_raises: bool
) -> None:
    """A denied webhook lookup must not hide setup or interrupt issue syncing."""
    monkeypatch.setenv("PRELOOP_URL", "https://preloop.example.com")
    tracker = _tracker(cloud_id=CLOUD_ID)
    tracker.jira_client = MagicMock()
    tracker.jira_client._session.get.side_effect = JIRAError(
        status_code=status_code, text="Webhook management unavailable"
    )
    blocked = MagicMock(status_code=status_code)
    if registration_raises:
        tracker.jira_client._session.post.side_effect = JIRAError(
            status_code=status_code, text="Webhook management unavailable"
        )
    else:
        tracker.jira_client._session.post.return_value = blocked
    tracker._post_webhook = MagicMock(return_value=blocked)
    org = models.Organization(
        id="22222222-2222-2222-2222-222222222222",
        identifier="example-site",
        webhook_secret="org-webhook-secret",
    )
    project = models.Project(id="project-1", identifier="PRJ")
    second = models.Project(id="project-2", identifier="OTHER")
    row = models.Tracker(
        id=tracker.tracker_id,
        tracker_type="jira",
        connection_details=dict(tracker.connection_details),
    )
    client = AsyncMock(spec=TrackerClient)
    client.client = tracker
    client.tracker_type = "jira"
    client.scan_projects.return_value = [project, second]
    client.scan_issues.return_value = ([MagicMock()], 0)
    db = MagicMock()

    def persist_details(
        db: object, *, db_obj: models.Tracker, obj_in: dict[str, Any]
    ) -> models.Tracker:
        db_obj.connection_details = obj_in["connection_details"]
        return db_obj

    with (
        patch("preloop.sync.trackers.jira.crud_tracker") as crud_tracker,
        patch("preloop.sync.trackers.jira.crud_webhook") as crud_webhook,
        patch("preloop.sync.scanner.core.crud_organization"),
        patch("preloop.api.endpoints.trackers.crud_organization") as api_orgs,
    ):
        crud_tracker.get.return_value = row
        crud_tracker.update.side_effect = persist_details
        crud_webhook.get_by_project_id.return_value = None
        api_orgs.get_for_tracker.return_value = [org]
        stats = await _process_organization(db, client, org, None, False)
        manual = _jira_manual_webhook(db, row, include_secret=True)

    assert stats["organizations"]["errors"] == 0
    assert stats["projects"] == 2
    assert stats["issues"] == 2
    assert client.scan_issues.await_count == 2
    tracker.jira_client._session.get.assert_called_once()
    tracker.jira_client._session.post.assert_called_once()
    assert row.connection_details["webhook_status"] == WEBHOOK_STATUS_MANUAL_REQUIRED
    assert manual is not None
    assert manual.url == (
        f"https://preloop.example.com/api/v1/private/webhooks/jira/{org.id}"
    )
    assert manual.secret == org.webhook_secret
    assert manual.events == [
        "jira:issue_created",
        "jira:issue_updated",
        "comment_created",
        "comment_updated",
    ]
    assert TOKEN not in str(manual.model_dump())


@pytest.mark.parametrize(
    ("scoped", "status_code", "expected_error"),
    [
        (False, 401, TrackerAuthenticationError),
        (False, 403, TrackerResponseError),
        (True, 500, TrackerResponseError),
    ],
)
def test_webhook_registration_preserves_other_library_errors(
    scoped: bool, status_code: int, expected_error: type[Exception]
) -> None:
    """Only scoped permission failures should enter manual setup fallback."""
    tracker = _tracker(**({"cloud_id": CLOUD_ID} if scoped else {}))
    tracker.jira_client = MagicMock()
    tracker.jira_client._session.post.side_effect = JIRAError(
        status_code=status_code, text="Registration failed"
    )
    tracker._register_webhook_alternate = MagicMock()
    with patch("preloop.sync.trackers.jira.crud_webhook") as crud_webhook:
        crud_webhook.get_by_project_id.return_value = None
        with pytest.raises(expected_error):
            tracker.register_webhook(
                db=MagicMock(),
                project=models.Project(id="project-1", identifier="PRJ"),
                webhook_url="https://preloop.example.com/webhook",
                secret="test-secret",
            )
    tracker._register_webhook_alternate.assert_not_called()


def test_classic_webhook_lookup_authentication_error_is_preserved() -> None:
    """A classic token's authentication failure remains visible to its caller."""
    tracker = _tracker()
    tracker.jira_client = MagicMock()
    tracker.jira_client._session.get.side_effect = JIRAError(
        status_code=401, text="Invalid credentials"
    )
    project = models.Project(id="project-1", identifier="PRJ")

    with pytest.raises(TrackerAuthenticationError):
        tracker.is_webhook_registered_for_project(
            project, "https://preloop.example.com/webhook"
        )


def test_library_init_keeps_a_classic_token_client():
    """A site that accepts the token still builds the jira library client."""
    sentinel = MagicMock()
    with patch("preloop.sync.trackers.jira.JIRA", return_value=sentinel) as ctor:
        tracker = JiraTracker(
            "tracker-1",
            TOKEN,
            {"url": SITE, "username": EMAIL},
            initialize_client=True,
        )
    assert tracker.jira_client is sentinel
    assert ctor.call_args.kwargs["server"] == SITE


@pytest.mark.asyncio
async def test_site_401_during_library_init_still_reaches_the_gateway():
    """create_tracker_client uses initialize_client=True.

    The library constructor fetches serverInfo on the site. A scoped token
    is rejected there; registration must still promote to the gateway.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "example-site.atlassian.net" and path == "/rest/api/2/myself":
            return httpx.Response(401, json={"message": "client must be authenticated"})
        if host == "example-site.atlassian.net" and path == "/_edge/tenant_info":
            return httpx.Response(200, json={"cloudId": CLOUD_ID})
        if (
            host == "api.atlassian.com"
            and path == f"/ex/jira/{CLOUD_ID}/rest/api/2/myself"
        ):
            return httpx.Response(200, json={"accountId": "abc", "displayName": "Ada"})
        return httpx.Response(599, json={"host": host, "path": path})

    with patch(
        "preloop.sync.trackers.jira.JIRA",
        side_effect=JIRAError(status_code=401, text="unauthorized"),
    ):
        client = await create_tracker_client(
            "jira",
            "tracker-1",
            TOKEN,
            {"url": SITE, "username": EMAIL},
        )
    assert isinstance(client, JiraTracker)
    assert client.jira_client is None
    seen = _mount(client, handler)
    result = await client.test_connection()
    assert result.connected is True
    assert client.cloud_id == CLOUD_ID
    assert client.jira_url == SITE
    assert any(request.url.host == "api.atlassian.com" for request in seen)


def test_dynamic_webhook_body_stores_created_webhook_id():
    """A 2xx from /rest/api/3/webhook carries createdWebhookId, not id/self."""
    tracker = _tracker()
    tracker.jira_client = None
    classic = httpx.Response(
        401,
        json={"message": "unauthorized"},
        request=httpx.Request("POST", f"{SITE}/rest/webhooks/1.0/webhook"),
    )
    dynamic = httpx.Response(
        200,
        json={"webhookRegistrationResult": [{"createdWebhookId": 42}]},
        request=httpx.Request("POST", f"{GATEWAY}/rest/api/3/webhook"),
    )

    def post(url: str, payload: dict) -> httpx.Response:
        if url.endswith("/rest/api/3/webhook"):
            return dynamic
        return classic

    tracker._post_webhook = post  # type: ignore[method-assign]
    project = SimpleNamespace(id="project-1", identifier="PRJ")
    with patch("preloop.sync.trackers.jira.crud_webhook") as crud_webhook:
        crud_webhook.get_by_project_id.return_value = None
        result = tracker.register_webhook(
            db=MagicMock(),
            project=project,
            webhook_url="https://preloop.example.com/api/v1/private/webhooks/jira/org-1",
            secret="org-webhook-secret",
        )
    assert result is True
    stored = crud_webhook.create.call_args.kwargs["obj_in"]
    assert stored["external_id"] == "42"
