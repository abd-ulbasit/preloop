"""Managed Jira Cloud grants (Atlassian OAuth 2.0 3LO) in the core tracker.

The provider plugin is not installed here: tests inject a fake credential
source, exactly like the plugin's ``managed_oauth_resolver:jira`` would.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import httpx
import pytest
from fastapi import HTTPException

from preloop.services import managed_credentials as mc
from preloop.services.managed_credentials import (
    ManagedCredential,
    ManagedCredentialUnavailableError,
    ManagedReconnectRequiredError,
)
from preloop.sync.event_normalizer import normalize_event_type
from preloop.sync.exceptions import (
    TrackerAuthenticationError,
    TrackerConnectionError,
    TrackerResponseError,
)
from preloop.sync.trackers import factory
from preloop.sync.trackers.jira import JiraTracker

CLOUD_ID = "11223344-aaaa-bbbb-cccc-556677889900"
SITE = "https://example-site.atlassian.net"
GATEWAY = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}"
DETAILS = {"auth_type": "managed_oauth", "cloud_id": CLOUD_ID, "site_url": SITE}


class FakeSource:
    """Returns token A until the clock passes A's expiry, then token B."""

    def __init__(self, tokens: List[str], lifetime: int = 3600):
        self.tokens = tokens
        self.lifetime = lifetime
        self.now = datetime.now(timezone.utc)
        self.index = 0
        self.calls: List[bool] = []
        self.issued_at = self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    async def __call__(self, *, force_refresh: bool = False) -> ManagedCredential:
        self.calls.append(force_refresh)
        expired = (self.now - self.issued_at).total_seconds() >= self.lifetime
        if (force_refresh or expired) and self.index + 1 < len(self.tokens):
            self.index += 1
            self.issued_at = self.now
        return ManagedCredential(
            access_token=self.tokens[self.index],
            expires_at=self.issued_at + timedelta(seconds=self.lifetime),
            rotation_version=self.index,
            provider="jira",
        )


def managed_tracker(source: Any, handler) -> JiraTracker:
    tracker = JiraTracker("t-1", "", dict(DETAILS), credential_source=source)
    tracker._transport = httpx.MockTransport(handler)
    return tracker


def recorder(responses: Dict[str, Any] | None = None, status: int = 200):
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = (responses or {}).get(request.url.path, {})
        return httpx.Response(status, json=body)

    return seen, handler


@pytest.mark.asyncio
async def test_managed_requests_use_gateway_bearer_and_rotate_token():
    source = FakeSource(["token-A", "token-B"])
    seen, handler = recorder(
        {
            f"/ex/jira/{CLOUD_ID}/rest/api/2/project": [
                {"id": "1", "key": "PRJ", "name": "P"}
            ]
        }
    )
    tracker = managed_tracker(source, handler)

    projects = await tracker.get_projects("org")
    source.advance(3700)  # past token A's lifetime
    await tracker.get_projects("org")

    assert [r.headers["authorization"] for r in seen] == [
        "Bearer token-A",
        "Bearer token-B",
    ]
    assert all(str(r.url).startswith(GATEWAY + "/rest/api/2/") for r in seen)
    # Human-facing links point at the site, never the gateway.
    assert projects[0]["url"] == f"{SITE}/projects/PRJ"
    assert tracker.managed_credential.access_token == "token-B"


@pytest.mark.asyncio
async def test_issue_and_comment_links_use_site_url():
    tracker = JiraTracker("t-1", "", dict(DETAILS), credential_source=FakeSource(["a"]))
    issue = tracker._map_jira_issue(
        {"id": "1", "key": "PRJ-7", "fields": {"summary": "x"}}, "PRJ"
    )
    assert issue.url == f"{SITE}/browse/PRJ-7"
    assert tracker.base_url == SITE and tracker.jira_url == SITE
    assert tracker.api_root == GATEWAY


@pytest.mark.asyncio
async def test_401_forces_exactly_one_refresh_then_reports():
    source = FakeSource(["token-A", "token-B"])
    seen, handler = recorder(status=401)
    tracker = managed_tracker(source, handler)

    with pytest.raises(TrackerAuthenticationError, match="Reconnect"):
        await tracker._make_request("GET", "myself")
    assert source.calls == [False, True]
    assert [r.headers["authorization"] for r in seen] == [
        "Bearer token-A",
        "Bearer token-B",
    ]


@pytest.mark.asyncio
async def test_redirect_is_refused_not_followed():
    seen: List[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    tracker = managed_tracker(FakeSource(["a"]), handler)
    with pytest.raises(TrackerResponseError, match="redirect"):
        await tracker._make_request("GET", "myself")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_resolver_missing_is_actionable_and_never_falls_back():
    tracker = JiraTracker("t-1", "", dict(DETAILS))
    seen, handler = recorder()
    tracker._transport = httpx.MockTransport(handler)
    with pytest.raises(TrackerAuthenticationError, match="no managed-provider plugin"):
        await tracker._make_request("GET", "myself")
    assert seen == []


@pytest.mark.asyncio
async def test_reconnect_required_surfaces_as_actionable_error():
    async def source(*, force_refresh=False):
        raise ManagedReconnectRequiredError("revoked", provider="jira")

    seen, handler = recorder()
    tracker = managed_tracker(source, handler)
    with pytest.raises(TrackerAuthenticationError, match="requires reconnect"):
        await tracker.test_connection_raw() if hasattr(
            tracker, "test_connection_raw"
        ) else await tracker._make_request("GET", "myself")
    assert seen == []


@pytest.mark.asyncio
async def test_unavailable_reported_by_test_connection():
    async def source(*, force_refresh=False):
        raise ManagedCredentialUnavailableError("outage", provider="jira")

    tracker = managed_tracker(source, recorder()[1])
    result = await tracker.test_connection()
    assert result.connected is False
    assert "outage" in result.message


def test_managed_rejects_pasted_token_bad_cloud_id_and_http_site():
    with pytest.raises(ValueError, match="pasted token"):
        JiraTracker("t", "secret", dict(DETAILS), credential_source=FakeSource(["a"]))
    with pytest.raises(ValueError, match="cloud_id"):
        JiraTracker(
            "t",
            "",
            {**DETAILS, "cloud_id": "../../x"},
            credential_source=FakeSource(["a"]),
        )
    with pytest.raises(ValueError, match="https"):
        JiraTracker(
            "t",
            "",
            {**DETAILS, "site_url": "http://x.atlassian.net"},
            credential_source=FakeSource(["a"]),
        )


def test_basic_auth_tracker_unchanged():
    tracker = JiraTracker(
        "t",
        "api-key",
        {"url": SITE + "/", "username": "me@example.com"},
        initialize_client=False,
    )
    assert tracker.managed is False
    assert tracker.api_root == SITE
    assert tracker.headers["Authorization"].startswith("Basic ")
    assert tracker.cloud_id is None


def test_webhook_transform_client_for_managed_tracker_needs_no_credential():
    tracker = JiraTracker("t", "", dict(DETAILS), initialize_client=False)
    assert tracker.managed and tracker.jira_url == SITE


@pytest.mark.asyncio
async def test_factory_builds_managed_jira_and_refuses_without_source():
    source = FakeSource(["a"])
    client = await factory.create_tracker_client(
        "jira", "t", "ignored-key", dict(DETAILS), credential_source=source
    )
    assert isinstance(client, JiraTracker) and client.managed
    assert client.api_key == ""
    # Managed details without a resolver never degrade to an unauthenticated client.
    assert await factory.create_tracker_client("jira", "t", "", dict(DETAILS)) is None
    assert (
        await factory.create_tracker_client(
            "github", "t", "", {"auth_type": "managed_oauth"}, credential_source=source
        )
        is None
    )


def test_credential_source_for_jira_ignores_repository_binding():
    tracker = SimpleNamespace(
        auth_type="managed_oauth",
        tracker_type="jira",
        account_id=uuid.uuid4(),
        id=uuid.uuid4(),
        connection_details={**DETAILS, "repository": "acme/repo"},
    )
    source = mc.tracker_credential_source(tracker)
    assert source.provider == "jira" and source.repository is None
    assert mc.managed_feature_flag("jira") == "jira_cloud_oauth"
    assert mc.managed_feature_flag("bitbucket") == "bitbucket_cloud_oauth"


class _HTTPError(Exception):
    def __init__(self, status_code, text="no"):
        super().__init__(text)
        self.status_code = status_code
        self.text = text


def test_handle_jira_error_maps_status():
    tracker = JiraTracker(
        "t", "k", {"url": SITE, "username": "u"}, initialize_client=False
    )
    with pytest.raises(TrackerAuthenticationError):
        tracker._handle_jira_error(_HTTPError(401), "x")
    with pytest.raises(TrackerResponseError, match="403"):
        tracker._handle_jira_error(_HTTPError(403), "x")
    with pytest.raises(TrackerConnectionError):
        tracker._handle_jira_error(Exception("boom"), "x")


def test_dynamic_webhook_jql():
    assert JiraTracker.dynamic_webhook_jql(["prj"]) == 'project = "PRJ"'
    assert JiraTracker.dynamic_webhook_jql(["B", "A", "a"]) == 'project IN ("A", "B")'
    # Project.identifier is Jira's numeric id; it is valid JQL too.
    assert JiraTracker.dynamic_webhook_jql(["10000"]) == 'project = "10000"'
    with pytest.raises(ValueError):
        JiraTracker.dynamic_webhook_jql(['A" OR project = "B'])
    with pytest.raises(ValueError):
        JiraTracker.dynamic_webhook_jql([])


@pytest.mark.asyncio
async def test_dynamic_webhook_register_refresh_delete_calls():
    seen: List[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(
                200, json={"webhookRegistrationResult": [{"createdWebhookId": 1000}]}
            )
        if request.method == "PUT":
            return httpx.Response(200, json={"expirationDate": "2026-11-11T00:00:00Z"})
        return httpx.Response(202)

    tracker = managed_tracker(FakeSource(["tok"]), handler)
    hook_url = "https://preloop.example/api/v1/private/webhooks/jira/org"
    assert await tracker.register_dynamic_webhook(hook_url, ["PRJ", "OPS"]) == "1000"
    assert await tracker.refresh_dynamic_webhooks(["1000"]) == "2026-11-11T00:00:00Z"
    await tracker.delete_dynamic_webhooks(["1000"])

    post, put, delete = seen
    assert str(post.url) == f"{GATEWAY}/rest/api/3/webhook"
    body = json.loads(post.content)
    assert body["url"] == hook_url
    assert body["webhooks"][0]["jqlFilter"] == 'project IN ("OPS", "PRJ")'
    assert "jira:issue_updated" in body["webhooks"][0]["events"]
    assert str(put.url) == f"{GATEWAY}/rest/api/3/webhook/refresh"
    assert json.loads(put.content) == {"webhookIds": [1000]}
    assert delete.method == "DELETE" and json.loads(delete.content) == {
        "webhookIds": [1000]
    }
    # The URL carries no secret and no token.
    assert "secret" not in body["url"] and "tok" not in body["url"]


@pytest.mark.asyncio
async def test_dynamic_webhook_registration_error_is_reported():
    def handler(request):
        return httpx.Response(
            200, json={"webhookRegistrationResult": [{"errors": ["bad JQL"]}]}
        )

    tracker = managed_tracker(FakeSource(["tok"]), handler)
    with pytest.raises(TrackerResponseError, match="bad JQL"):
        await tracker.register_dynamic_webhook("https://x/h", ["PRJ"])


class FakeWebhookCrud:
    def __init__(self):
        self.rows: Dict[Any, Any] = {}
        self.removed: List[Any] = []

    def jira_callback_context(self, db, *, tracker_id):
        return CLOUD_ID, "jira-user"

    def jira_callback_namespace(self, db, *, callback_url, matched_ids=None):
        return None

    def set_jira_callback_namespace(self, db, **kwargs):
        for row in self.rows.values():
            if (
                row.external_id == kwargs["external_id"]
                and row.url == kwargs["callback_url"]
            ):
                row.jira_cloud_id = kwargs["cloud_id"]
                row.jira_provider_subject = kwargs["provider_subject"]

    def get_by_project_id(self, db, *, project_id):
        return self.rows.get(project_id)

    def remove(self, db, *, id):
        self.removed.append(id)
        for key, row in list(self.rows.items()):
            if row.id == id:
                del self.rows[key]

    def create(self, db, *, obj_in):
        row = SimpleNamespace(id=uuid.uuid4(), **obj_in)
        self.rows[obj_in["project_id"]] = row
        return row


@pytest.mark.asyncio
async def test_sync_dynamic_webhooks_registers_then_refreshes_then_reregisters(
    monkeypatch,
):
    crud = FakeWebhookCrud()
    monkeypatch.setattr("preloop.sync.trackers.jira.crud_webhook", crud)
    created = iter([1000, 1001, 1002])
    seen: List[httpx.Request] = []
    live = {"ids": []}

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"values": [{"id": i} for i in live["ids"]], "isLast": True},
            )
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "webhookRegistrationResult": [{"createdWebhookId": next(created)}]
                },
            )
        if request.method == "PUT":
            return httpx.Response(200, json={"expirationDate": "later"})
        return httpx.Response(202)

    tracker = managed_tracker(FakeSource(["tok"]), handler)
    # Jira projects store the numeric id as identifier and the key as slug.
    p1 = SimpleNamespace(id=uuid.uuid4(), identifier="10000", slug="PRJ")
    p2 = SimpleNamespace(id=uuid.uuid4(), identifier="10001", slug="OPS")
    url = "https://preloop.example/api/v1/private/webhooks/jira/org"

    first = await tracker.sync_dynamic_webhooks(None, [p1], url)
    assert first["action"] == "registered" and first["webhook_id"] == "1000"
    assert crud.rows[p1.id].secret == ""
    post = [r for r in seen if r.method == "POST"][-1]
    assert json.loads(post.content)["webhooks"][0]["jqlFilter"] == 'project = "PRJ"'

    live["ids"] = [1000]
    second = await tracker.sync_dynamic_webhooks(None, [p1], url)
    assert second["action"] == "refreshed"
    assert seen[-1].method == "PUT"

    # Removed on the Jira side (or purged after expiry): re-registered, not
    # refreshed forever.
    live["ids"] = []
    third = await tracker.sync_dynamic_webhooks(None, [p1], url)
    assert third == {"action": "registered", "webhook_id": "1001"}
    assert not any(r.method == "PUT" for r in seen[-3:])

    live["ids"] = [1001]
    fourth = await tracker.sync_dynamic_webhooks(None, [p1, p2], url)
    assert fourth == {"action": "registered", "webhook_id": "1002"}
    assert {crud.rows[p.id].external_id for p in (p1, p2)} == {"1002"}
    assert seen[-1].method == "DELETE"
    assert json.loads(seen[-1].content) == {"webhookIds": [1001]}


def _dynamic_payload(items):
    return {
        "timestamp": 1790000000000,
        "webhookEvent": "jira:issue_updated",
        "issue_event_type_name": "issue_generic",
        "matchedWebhookIds": [1000],
        "issue": {
            "id": "10001",
            "key": "PRJ-12",
            "fields": {
                "summary": "x",
                "labels": ["agent"],
                "status": {"name": "In Progress"},
                "project": {"id": "1", "key": "PRJ"},
            },
        },
        "changelog": {"id": "1", "items": items},
    }


def test_dynamic_payload_normalises_like_classic():
    labels = [{"field": "labels", "fromString": "", "toString": "agent"}]
    status = [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]
    assert (
        normalize_event_type("jira", "jira:issue_updated", _dynamic_payload(labels))
        == "issue_labeled"
    )
    assert (
        normalize_event_type("jira", "jira:issue_updated", _dynamic_payload(status))
        == "issue_status_changed"
    )


class _Plan:
    def __init__(self):
        self.tasks = []

    def queue_task(self, name, **kwargs):
        self.tasks.append(name)


@pytest.fixture
def verifier_env(monkeypatch):
    from preloop.api.endpoints import webhooks

    monkeypatch.setattr(webhooks, "_managed_jira_webhook_ids", lambda db, t: {"1000"})
    yield webhooks
    mc.register_managed_webhook_verifier("jira", None)


def test_managed_delivery_requires_valid_jwt_and_matching_webhook(verifier_env):
    webhooks = verifier_env
    tracker = SimpleNamespace(id=uuid.uuid4())
    body = json.dumps(
        {"webhookEvent": "jira:issue_updated", "matchedWebhookIds": [1000]}
    )

    class Verifier:
        def __init__(self, ok):
            self.ok = ok
            self.seen = []

        def verify(self, *, authorization):
            self.seen.append(authorization)
            return self.ok

    # No verifier installed (plugin missing): denied.
    with pytest.raises(HTTPException) as exc:
        webhooks._verify_managed_jira_delivery(
            headers={"Authorization": "Bearer x"},
            raw_body=body.encode(),
            db=None,
            tracker=tracker,
            plan=_Plan(),
        )
    assert exc.value.status_code == 403

    bad = Verifier(False)
    mc.register_managed_webhook_verifier("jira", bad)
    with pytest.raises(HTTPException):
        webhooks._verify_managed_jira_delivery(
            headers={"Authorization": "Bearer x"},
            raw_body=body.encode(),
            db=None,
            tracker=tracker,
            plan=_Plan(),
        )

    good = Verifier(True)
    mc.register_managed_webhook_verifier("jira", good)
    webhooks._verify_managed_jira_delivery(
        headers={"Authorization": "Bearer jwt"},
        raw_body=body.encode(),
        db=None,
        tracker=tracker,
        plan=_Plan(),
    )
    assert good.seen == ["Bearer jwt"]

    # Signed by the app but for another tenant's webhook: denied.
    other = json.dumps({"webhookEvent": "jira:issue_updated", "matchedWebhookIds": [7]})
    with pytest.raises(HTTPException):
        webhooks._verify_managed_jira_delivery(
            headers={"Authorization": "Bearer jwt"},
            raw_body=other.encode(),
            db=None,
            tracker=tracker,
            plan=_Plan(),
        )


@pytest.mark.asyncio
async def test_token_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    seen, handler = recorder(status=500)
    tracker = managed_tracker(FakeSource(["very-secret-token"]), handler)
    with pytest.raises(TrackerResponseError) as exc:
        await tracker._make_request("GET", "myself")
    assert "very-secret-token" not in str(exc.value)
    assert "very-secret-token" not in caplog.text


def test_managed_jira_edit_keeps_site_binding_and_drops_credentials():
    from preloop.api.endpoints.trackers import _merge_managed_jira_details

    previous = {
        **DETAILS,
        "site_name": "example-site",
        "actor": {"display_name": "A"},
        "managed_state": "connected",
    }
    merged = _merge_managed_jira_details(
        previous,
        {
            "repository": "acme/repo",
            "username": "x@y",
            "api_key": "k",
            "site_name": "z",
        },
    )
    assert merged["cloud_id"] == CLOUD_ID and merged["site_url"] == SITE
    assert merged["site_name"] == "example-site"
    assert merged["auth_type"] == "managed_oauth"
    assert merged["actor"] == {"display_name": "A"}
    assert merged["repository"] == "acme/repo"
    assert "username" not in merged and "api_key" not in merged

    with pytest.raises(HTTPException) as exc:
        _merge_managed_jira_details(previous, {"cloud_id": "other-cloud-id-123"})
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        _merge_managed_jira_details(
            previous, {"site_url": "https://evil.atlassian.net"}
        )


@pytest.mark.asyncio
async def test_shared_callback_conflict_registers_own_id_then_reconciles(monkeypatch):
    """The existing owner's hook is neither reused as our id nor deleted."""
    crud = FakeWebhookCrud()
    anchor_id = uuid.uuid4()
    old_project_id = uuid.uuid4()
    callback = f"https://preloop.example/api/v1/private/webhooks/jira/{anchor_id}"
    requested = f"https://preloop.example/api/v1/private/webhooks/jira/{uuid.uuid4()}"
    existing = crud.create(
        None, obj_in={"project_id": old_project_id, "external_id": "1", "url": callback}
    )
    crud.jira_callback_context = lambda *a, **kw: (CLOUD_ID, "jira-user")
    crud.get_jira_callback_projects = lambda *a, **kw: [
        SimpleNamespace(organization_id=anchor_id)
    ]
    monkeypatch.setattr("preloop.sync.trackers.jira.crud_webhook", crud)
    monkeypatch.setattr(
        "preloop.sync.trackers.jira.crud_organization.get_with_tracker",
        lambda *a, **kw: SimpleNamespace(tracker_id="anchor-tracker"),
    )
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(
                200, json={"values": [{"id": 1}, {"id": 2}], "isLast": True}
            )
        if request.method == "PUT":
            return httpx.Response(200, json={"expirationDate": "later"})
        body = json.loads(request.content)
        if body["url"] != callback:
            return httpx.Response(
                200,
                json={
                    "webhookRegistrationResult": [
                        {
                            "errors": [
                                "Only a single URL per user is allowed to be registered via REST API. The currently used URL: "
                                + callback
                            ]
                        }
                    ]
                },
            )
        return httpx.Response(
            200, json={"webhookRegistrationResult": [{"createdWebhookId": 2}]}
        )

    tracker = managed_tracker(FakeSource(["tok"]), handler)
    project = SimpleNamespace(id=uuid.uuid4(), identifier="10000", slug="PRJ")
    assert await tracker.sync_dynamic_webhooks(None, [project], requested) == {
        "action": "registered",
        "webhook_id": "2",
    }
    assert crud.rows[project.id].url == callback
    assert crud.rows[project.id].external_id == "2"
    assert crud.rows[project.id].jira_cloud_id == CLOUD_ID
    assert crud.rows[project.id].jira_provider_subject == "jira-user"
    assert crud.rows[old_project_id] is existing
    assert crud.removed == []
    assert [json.loads(r.content)["url"] for r in seen if r.method == "POST"] == [
        requested,
        callback,
    ]
    assert (await tracker.sync_dynamic_webhooks(None, [project], requested))[
        "action"
    ] == "refreshed"
    assert len([r for r in seen if r.method == "POST"]) == 2
    assert not any(r.method == "DELETE" for r in seen)
