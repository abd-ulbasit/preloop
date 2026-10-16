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


def test_expired_access_with_live_refresh_is_usable(db_session, callback_owner):
    tracker, config, token, project, hook, callback = callback_owner
    token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    token.refresh_token_encrypted = "synthetic"
    token.refresh_token_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    db_session.flush()
    assert owners(db_session, callback) == [project]


def test_shared_callback_ids_preserve_account_ownership(db_session, callback_owner):
    first_tracker, _, _, first_project, _, callback = callback_owner
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
    second_tracker, _, _, second_project, second_hook, _ = make_owner(db_session, user)
    second_hook.url = callback
    second_hook.external_id = "2"
    db_session.flush()
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
