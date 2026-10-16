"""Security boundaries for a Jira OAuth user's shared callback."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from preloop.api.endpoints import webhooks
from preloop.sync.exceptions import TrackerResponseError
from preloop.sync.trackers.jira import JiraTracker


@pytest.fixture
def routing(monkeypatch):
    anchor_id = str(uuid4())
    tracker = SimpleNamespace(id="tracker", account_id="account")
    anchor = SimpleNamespace(id=anchor_id, tracker_id=tracker.id, tracker=tracker)
    project = SimpleNamespace(
        identifier="10000",
        slug="PRJ",
        organization_id=anchor_id,
        organization=SimpleNamespace(id=anchor_id, identifier="site", tracker=tracker),
    )
    monkeypatch.setattr(webhooks, "is_managed_tracker", lambda _: True)
    monkeypatch.setattr(
        webhooks.crud_organization, "get_with_tracker", lambda *a, **kw: anchor
    )
    monkeypatch.setattr(
        webhooks.crud_webhook,
        "jira_callback_context",
        lambda *a, **kw: ("cloud", "user"),
    )
    lookup = Mock(return_value=[project, project])
    monkeypatch.setattr(webhooks.crud_webhook, "get_jira_callback_projects", lookup)
    verifier = Mock()
    verifier.verify.return_value = True
    monkeypatch.setattr(webhooks, "get_managed_webhook_verifier", lambda _: verifier)
    rules = [
        SimpleNamespace(
            scope_type="ORGANIZATION", rule_type="INCLUDE", identifier="site"
        )
    ]
    monkeypatch.setattr(
        webhooks.crud_tracker_scope_rule, "get_by_tracker", lambda *a, **kw: rules
    )
    original_dispatch = webhooks._prepare_webhook
    dispatch = Mock(return_value={"status": "success"})
    monkeypatch.setattr(webhooks, "_prepare_webhook", dispatch)
    payload = {
        "matchedWebhookIds": [1, 1],
        "issue": {"fields": {"project": {"id": "10000", "key": "PRJ"}}},
    }
    return SimpleNamespace(
        anchor=anchor,
        project=project,
        lookup=lookup,
        verifier=verifier,
        rules=rules,
        dispatch=dispatch,
        original_dispatch=original_dispatch,
        payload=payload,
    )


def prepare(routing):
    return webhooks._prepare_shared_jira_webhook(
        routing.anchor.id,
        json.dumps(routing.payload).encode(),
        {},
        None,
        webhooks._WebhookPlan(),
    )


def test_dispatch_uses_persisted_ids_and_deduplicates_owners(routing):
    assert prepare(routing)["matched_trackers"] == 1
    assert routing.lookup.call_args.kwargs["matched_ids"] == {"1"}
    assert routing.lookup.call_args.kwargs["cloud_id"] == "cloud"
    assert routing.lookup.call_args.kwargs["provider_subject"] == "user"
    routing.dispatch.assert_called_once()
    assert routing.dispatch.call_args.args[1] == routing.anchor.id


@pytest.mark.parametrize(
    "issue", [[], {"fields": []}, {"fields": {"project": "invalid"}}, None]
)
def test_malformed_project_never_dispatches(routing, issue):
    routing.payload["issue"] = issue
    with pytest.raises(HTTPException) as exc:
        prepare(routing)
    assert exc.value.status_code == 403
    routing.dispatch.assert_not_called()


def test_unowned_id_never_dispatches(routing):
    routing.lookup.return_value = []
    with pytest.raises(HTTPException):
        prepare(routing)
    routing.dispatch.assert_not_called()


@pytest.mark.parametrize("failure", [False, RuntimeError("verifier unavailable")])
def test_signature_failure_precedes_owner_lookup(routing, failure):
    if isinstance(failure, Exception):
        routing.verifier.verify.side_effect = failure
    else:
        routing.verifier.verify.return_value = failure
    with pytest.raises(HTTPException) as exc:
        prepare(routing)
    assert exc.value.status_code == 403
    routing.lookup.assert_not_called()
    routing.dispatch.assert_not_called()


def test_current_project_scope_exclusion_prevents_dispatch(routing):
    routing.rules.append(
        SimpleNamespace(scope_type="PROJECT", rule_type="EXCLUDE", identifier="PRJ")
    )
    assert prepare(routing)["matched_trackers"] == 0
    routing.dispatch.assert_not_called()


def test_other_project_never_dispatches(routing):
    routing.payload["issue"]["fields"]["project"] = {"id": "20000", "key": "OTHER"}
    assert prepare(routing)["matched_trackers"] == 0
    routing.dispatch.assert_not_called()


@pytest.mark.parametrize(
    "callback",
    [
        "https://evil.example/api/v1/private/webhooks/jira/{}",
        "https://preloop.example/api/v1/private/webhooks/jira/{}?redirect=1",
        "https://preloop.example/api/v1/private/webhooks/jira/not-a-uuid",
        "https://preloop.example/other/{}",
    ],
)
def test_callback_conflict_cannot_select_external_or_malformed_url(callback):
    tracker = JiraTracker(
        "tracker", "", {"url": "https://jira.example"}, initialize_client=False
    )
    requested = f"https://preloop.example/api/v1/private/webhooks/jira/{uuid4()}"
    with pytest.raises(TrackerResponseError):
        tracker._validated_shared_callback(None, requested, callback.format(uuid4()))


def test_target_subscription_still_filters_shared_delivery(routing, monkeypatch):
    tracker = routing.anchor.tracker
    tracker.is_active = True
    tracker.subscribed_events = ["jira:issue_created"]
    routing.anchor.webhook_secret = None
    routing.payload["webhookEvent"] = "jira:issue_updated"
    monkeypatch.setattr(webhooks, "_prepare_webhook", routing.original_dispatch)
    monkeypatch.setattr(webhooks, "_managed_jira_webhook_ids", lambda *a: {"1"})
    touched = Mock()
    monkeypatch.setattr(webhooks.crud_organization, "touch_webhook", touched)
    plan = webhooks._WebhookPlan()
    result = webhooks._prepare_shared_jira_webhook(
        routing.anchor.id, json.dumps(routing.payload).encode(), {}, Mock(), plan
    )
    assert result["matched_trackers"] == 1
    assert plan.tasks == []
    touched.assert_called_once()


def test_jira_retry_identity_is_stable_and_distinguishes_events():
    first = b'{"timestamp": 1, "issue": {"id": "10000"}}'
    second = b'{"timestamp": 2, "issue": {"id": "10000"}}'
    assert webhooks._jira_delivery_identity(
        first, {}
    ) == webhooks._jira_delivery_identity(first, {})
    assert webhooks._jira_delivery_identity(
        first, {}
    ) != webhooks._jira_delivery_identity(second, {})
    assert (
        webhooks._jira_delivery_identity(
            first, {"X-Atlassian-Webhook-Identifier": "delivery-1"}
        )
        == "delivery-1"
    )


def test_shared_callback_dispatches_to_stored_target_not_path_owner(routing):
    target_id = str(uuid4())
    routing.project.organization_id = target_id
    routing.project.organization.id = target_id
    assert prepare(routing)["matched_trackers"] == 1
    assert routing.dispatch.call_args.args[1] == target_id
    assert target_id != routing.anchor.id


@pytest.mark.parametrize("authorized", [False, True])
def test_shared_callback_requires_owned_registration_and_matching_anchor(
    routing, monkeypatch, authorized
):
    tracker = JiraTracker(
        "tracker", "", {"url": "https://jira.example"}, initialize_client=False
    )
    requested = f"https://preloop.example/api/v1/private/webhooks/jira/{uuid4()}"
    callback = (
        f"https://preloop.example/api/v1/private/webhooks/jira/{routing.anchor.id}"
    )
    if not authorized:
        routing.lookup.return_value = []
        with pytest.raises(TrackerResponseError):
            tracker._validated_shared_callback(None, requested, callback)
    else:
        # A retired anchor remains a namespace; only active persisted target
        # owners returned by CRUD can receive deliveries through it.
        routing.anchor.tracker.is_active = False
        assert tracker._validated_shared_callback(None, requested, callback) == callback
        monkeypatch.setattr(
            webhooks.crud_webhook,
            "jira_callback_context",
            lambda *a, **kw: (
                ("cloud", "user")
                if kw["tracker_id"] == "tracker"
                else ("other", "user")
            ),
        )
        routing.anchor.tracker_id = "different"
        with pytest.raises(TrackerResponseError):
            tracker._validated_shared_callback(None, requested, callback)
