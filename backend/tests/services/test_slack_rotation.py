"""Encrypted refresh rotation belongs to one replica and preserves old state on failure."""

from __future__ import annotations
import json
import os
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from preloop.models import models
from preloop.models.crud import crud_chat
from preloop.services.slack_oauth import refresh_slack_connection
from preloop.utils.encryption import encrypt_value, decrypt_value
from preloop.services.chat_providers import ChatProviderError


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_only_current_worker_can_persist_rotated_credentials(
    monkeypatch, failure: bool
) -> None:
    url = os.getenv("CHAT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires isolated migrated CHAT_TEST_DATABASE_URL")
    engine = create_engine(url)
    with Session(engine) as db:
        account = models.Account(organization_name="Synthetic Slack rotation")
        db.add(account)
        db.flush()
        old = encrypt_value(
            json.dumps(
                {
                    "bot_token": "old-token",
                    "verification_secret": "signing",
                    "bot_user_id": "bot",
                    "base_url": "",
                    "slack_refresh_token": "old-refresh",
                    "slack_token_expires_at": time.time() - 1,
                }
            )
        )
        connection = models.ChatConnection(
            account_id=account.id,
            provider="slack",
            workspace_id="workspace",
            name="Synthetic Slack",
            credentials_encrypted=old,
        )
        db.add(connection)
        db.commit()
        aid = account.id
        try:
            assert crud_chat.claim_listener(db, connection.id, "owner")
            assert not crud_chat.rotate_credentials(
                db, connection.id, "wrong", old, "forged"
            )
            exchange = (
                AsyncMock(side_effect=ChatProviderError("Unavailable"))
                if failure
                else AsyncMock(
                    return_value={
                        "access_token": "new-token",
                        "refresh_token": "new-refresh",
                        "expires_in": 43200,
                    }
                )
            )
            monkeypatch.setattr("preloop.services.slack_oauth.exchange_slack", exchange)
            assert (
                await refresh_slack_connection(db, connection, "owner") is not failure
            )
            current = crud_chat.connections.get(db, connection.id)
            db.refresh(current)
            state = json.loads(decrypt_value(current.credentials_encrypted))
            assert state["bot_token"] == ("old-token" if failure else "new-token")
            assert state["slack_refresh_token"] == (
                "old-refresh" if failure else "new-refresh"
            )
            if not failure:
                assert state["slack_token_expires_at"] > time.time()
        finally:
            db.query(models.Account).filter_by(id=aid).delete()
            db.commit()
    engine.dispose()


def test_oauth_button_requires_fixed_https_redirect_allowlist(monkeypatch) -> None:
    from preloop.api.endpoints.chat_setup import oauth_configured
    from preloop.config import settings

    monkeypatch.setattr(settings, "preloop_url", "https://preloop.example.com")
    for name in ("CLIENT_ID", "CLIENT_SECRET", "SIGNING_SECRET"):
        monkeypatch.setenv("CHAT_SLACK_" + name, "synthetic")
    monkeypatch.setenv("CHAT_SLACK_REDIRECT_URI", "https://other.example.com/callback")
    assert not oauth_configured()
    monkeypatch.setenv(
        "CHAT_SLACK_REDIRECT_URI",
        "https://preloop.example.com/api/v1/chat/setup/slack/callback",
    )
    assert oauth_configured()
