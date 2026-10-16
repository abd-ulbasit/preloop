"""Persisted ownership and grant validity for shared Jira callbacks."""

from datetime import datetime, timedelta, timezone

import pytest

from preloop.models import models
from preloop.models.crud import crud_webhook


def make_owner(db_session, test_user):
    account_id = test_user.account_id
    tracker = models.Tracker(
        account_id=account_id,
        name="Jira",
        tracker_type="jira",
        auth_type="managed_oauth",
        connection_details={"cloud_id": "cloud"},
    )
    secret = models.SecretReference(
        account_id=account_id,
        name="Client secret",
        backend_type="local_encrypted",
        secret_kind="oauth_client_secret",
        encrypted_value="synthetic",
    )
    db_session.add_all([tracker, secret])
    db_session.flush()
    config = models.OAuthProviderConfiguration(
        account_id=account_id,
        provider="jira",
        canonical_instance="https://jira.example",
        client_id="client",
        client_secret_id=secret.id,
        callback_uri="https://preloop.example/oauth/callback",
        selected_permissions=[],
    )
    db_session.add(config)
    db_session.flush()
    token = models.OAuthToken(
        account_id=account_id,
        user_id=test_user.id,
        tracker_id=tracker.id,
        provider="jira",
        auth_mode="managed_oauth",
        status="active",
        configuration_id=config.id,
        configuration_version=config.version,
        provider_subject="user",
        canonical_instance=config.canonical_instance,
        access_token_encrypted="synthetic",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    org = models.Organization(name="Site", identifier="site", tracker_id=tracker.id)
    db_session.add_all([token, org])
    db_session.flush()
    project = models.Project(
        name="Project", identifier="10000", slug="PRJ", organization_id=org.id
    )
    db_session.add(project)
    db_session.flush()
    callback = f"https://preloop.example/api/v1/private/webhooks/jira/{org.id}"
    hook = models.Webhook(
        project_id=project.id, external_id="1", url=callback, secret="", events=[]
    )
    db_session.add(hook)
    db_session.flush()
    return tracker, config, token, project, hook, callback


@pytest.fixture
def callback_owner(db_session, test_user):
    return make_owner(db_session, test_user)


def owners(db, callback, **overrides):
    return crud_webhook.get_jira_callback_projects(
        db,
        callback_url=callback,
        cloud_id=overrides.get("cloud_id", "cloud"),
        provider_subject=overrides.get("subject", "user"),
        matched_ids=overrides.get("ids", {"1"}),
    )


def test_callback_requires_exact_persisted_namespace(db_session, callback_owner):
    tracker, config, token, project, hook, callback = callback_owner
    assert owners(db_session, callback) == [project]
    assert owners(db_session, callback + "/") == []
    assert owners(db_session, callback, cloud_id="other") == []
    assert owners(db_session, callback, subject="other") == []
    assert owners(db_session, callback, ids={"2"}) == []
    hook.organization_id = project.organization_id
    db_session.flush()
    assert owners(db_session, callback) == []


@pytest.mark.parametrize(
    "change", ["revoked", "expired", "disabled", "version", "inactive"]
)
def test_callback_excludes_unusable_grants(db_session, callback_owner, change):
    tracker, config, token, project, hook, callback = callback_owner
    if change == "revoked":
        token.status = "disconnected"
    elif change == "expired":
        token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        token.refresh_token_encrypted = "synthetic"
        token.refresh_token_expires_at = token.expires_at
    elif change == "disabled":
        config.enabled = False
    elif change == "version":
        config.version += 1
    else:
        tracker.is_active = False
    db_session.flush()
    assert owners(db_session, callback) == []
    if change in {"revoked", "expired"}:
        assert (
            crud_webhook.jira_callback_context(db_session, tracker_id=str(tracker.id))
            is None
        )


def test_expired_access_with_live_refresh_is_usable(db_session, callback_owner):
    tracker, config, token, project, hook, callback = callback_owner
    token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    token.refresh_token_encrypted = "synthetic"
    token.refresh_token_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    db_session.flush()
    assert owners(db_session, callback) == [project]
    assert crud_webhook.jira_callback_context(
        db_session, tracker_id=str(tracker.id)
    ) == ("cloud", "user")


def test_shared_callback_ids_preserve_account_ownership(db_session, callback_owner):
    first_tracker, _, _, first_project, _, callback = callback_owner
    second_tracker, _, _, second_project, second_hook, _ = second_owner(
        db_session, callback
    )
    assert first_tracker.account_id != second_tracker.account_id
    assert owners(db_session, callback, ids={"1"}) == [first_project]
    assert owners(db_session, callback, ids={"2"}) == [second_project]
    assert {p.id for p in owners(db_session, callback, ids={"1", "2"})} == {
        first_project.id,
        second_project.id,
    }
    second_tracker.connection_details = {"cloud_id": "other-cloud"}
    db_session.flush()
    assert owners(db_session, callback, ids={"1", "2"}) == [first_project]


def second_owner(db_session, callback):
    account = models.Account(organization_name="Second account", is_active=True)
    db_session.add(account)
    db_session.flush()
    user = models.User(
        account_id=account.id,
        email="second@example.com",
        username="second",
        hashed_password="synthetic",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    result = make_owner(db_session, user)
    second_hook = result[4]
    second_hook.url = callback
    second_hook.external_id = "2"
    db_session.flush()
    return result


def test_real_anchor_disconnect_preserves_only_active_registered_target(
    db_session, callback_owner, monkeypatch
):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock

    from preloop.api.endpoints import webhooks
    from preloop.models.crud.managed_oauth import CRUDManagedOAuth

    first_tracker, _, first_token, first_project, first_hook, callback = callback_owner
    second_tracker, _, second_token, second_project, second_hook, _ = second_owner(
        db_session, callback
    )
    for tracker, hook in [(first_tracker, first_hook), (second_tracker, second_hook)]:
        crud_webhook.set_jira_callback_namespace(
            db_session,
            tracker_id=str(tracker.id),
            external_id=hook.external_id,
            callback_url=callback,
            cloud_id="cloud",
            provider_subject="user",
        )
    db_session.flush()
    store = CRUDManagedOAuth(engine=db_session.connection())
    store.disconnect(
        account_id=first_tracker.account_id,
        grant_id=first_token.id,
        expected_version=first_token.rotation_version,
    )
    db_session.expire_all()
    assert first_token.tracker_id is None
    assert first_token.status == "disconnected"
    assert not first_tracker.is_active
    assert (
        crud_webhook.jira_callback_context(db_session, tracker_id=str(first_tracker.id))
        is None
    )
    assert crud_webhook.jira_callback_namespace(
        db_session, callback_url=callback, matched_ids={"1", "2"}
    ) == ("cloud", "user")
    assert owners(db_session, callback, ids={"1", "2"}) == [second_project]
    monkeypatch.setattr(
        webhooks,
        "get_managed_webhook_verifier",
        lambda _: SimpleNamespace(verify=lambda **kw: True),
    )
    monkeypatch.setattr(
        webhooks.crud_tracker_scope_rule,
        "get_by_tracker",
        lambda *a, **kw: [
            SimpleNamespace(
                scope_type="ORGANIZATION", rule_type="INCLUDE", identifier="site"
            )
        ],
    )
    dispatch = Mock(return_value={"status": "success"})
    monkeypatch.setattr(webhooks, "_prepare_webhook", dispatch)
    from preloop.config import settings

    monkeypatch.setattr(settings, "preloop_url", "https://preloop.example")
    payload = {
        "matchedWebhookIds": [1, 2],
        "issue": {"fields": {"project": {"id": "10000", "key": "PRJ"}}},
    }
    result = webhooks._prepare_shared_jira_webhook(
        str(first_project.organization_id),
        json.dumps(payload).encode(),
        {},
        db_session,
        webhooks._WebhookPlan(),
    )
    assert result["matched_trackers"] == 1
    assert dispatch.call_args.args[1] == str(second_project.organization_id)
    from preloop.sync.trackers.jira import JiraTracker

    client = JiraTracker(
        str(second_tracker.id),
        "",
        {"url": "https://jira.example"},
        initialize_client=False,
    )
    requested = f"https://preloop.example/api/v1/private/webhooks/jira/{second_project.organization_id}"
    assert (
        client._validated_shared_callback(db_session, requested, callback) == callback
    )
    store.disconnect(
        account_id=second_tracker.account_id,
        grant_id=second_token.id,
        expected_version=second_token.rotation_version,
    )
    db_session.expire_all()
    dispatch.reset_mock()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        webhooks._prepare_shared_jira_webhook(
            str(first_project.organization_id),
            json.dumps(payload).encode(),
            {},
            db_session,
            webhooks._WebhookPlan(),
        )
    assert exc.value.status_code == 403
    dispatch.assert_not_called()


def test_namespace_migration_roundtrip_preserves_legacy_hook(
    db_session, callback_owner
):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    migration = importlib.import_module(
        "preloop.models.alembic.versions.20261020_jira_callback_namespace"
    )
    connection = db_session.connection()
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        assert "jira_cloud_id" not in {
            c["name"] for c in inspect(connection).get_columns("webhook")
        }
        migration.upgrade()
    db_session.expire_all()
    hook = callback_owner[4]
    assert hook.external_id == "1"
    assert hook.jira_cloud_id is None
    assert hook.jira_provider_subject is None
