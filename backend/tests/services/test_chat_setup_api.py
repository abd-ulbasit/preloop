"""Management permissions, draft binding, OAuth replay and body-size boundaries."""

from __future__ import annotations
import hashlib
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from preloop.api.endpoints import chat_setup
from preloop.models import models
from preloop.models.crud import crud_chat


@pytest.mark.asyncio
async def test_discovery_denied_before_provider_lookup_without_management_permission(
    monkeypatch,
) -> None:
    app = FastAPI()
    app.include_router(chat_setup.router)
    app.dependency_overrides[chat_setup.get_db_session] = lambda: object()
    app.dependency_overrides[chat_setup.get_current_active_user] = lambda: (
        SimpleNamespace(id=uuid4(), account_id=uuid4())
    )
    monkeypatch.setattr(chat_setup, "require_active_account", lambda *args: None)
    monkeypatch.setattr(chat_setup, "ensure_permission_in_oss", lambda *args: None)
    monkeypatch.setattr(chat_setup, "has_permission", lambda *args: False)

    async def forbidden(*args):
        raise AssertionError("No provider request may run before authorization")

    monkeypatch.setattr(chat_setup, "discover", forbidden)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat/setup", json={"provider": "slack", "bot_token": "secret"}
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_setup_bounds_stream_before_json_parsing() -> None:
    app = FastAPI()
    app.include_router(chat_setup.router)

    async def chunks():
        for _ in range(5):
            yield b"x" * 8192

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat/setup",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


def test_draft_actor_account_expiry_and_oauth_replay() -> None:
    url = os.getenv("CHAT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires isolated migrated CHAT_TEST_DATABASE_URL")
    engine = create_engine(url)
    with Session(engine) as db:
        account = models.Account(organization_name="Synthetic draft")
        db.add(account)
        db.flush()
        user = models.User(
            account_id=account.id,
            username="draft-" + uuid4().hex,
            email="member@example.com",
            hashed_password="unused",
            is_active=True,
        )
        db.add(user)
        db.commit()
        account_id, user_id = account.id, user.id
        digest = hashlib.sha256(uuid4().bytes).hexdigest()
        try:
            draft = crud_chat.drafts.create(
                db,
                obj_in={
                    "account_id": account_id,
                    "user_id": user_id,
                    "provider": "slack",
                    "state_encrypted": "encrypted",
                    "expires_at": datetime.utcnow() + timedelta(minutes=10),
                    "oauth_digest": digest,
                },
            )
            assert crud_chat.setup_draft(db, draft.id, account_id, user_id)
            assert crud_chat.setup_draft(db, draft.id, uuid4(), user_id) is None
            assert crud_chat.setup_draft(db, draft.id, account_id, uuid4()) is None
            assert crud_chat.oauth_draft(db, digest).id == draft.id
            assert crud_chat.oauth_draft(db, digest) is None
            crud_chat.drafts.update(
                db,
                db_obj=draft,
                obj_in={"expires_at": datetime.utcnow() - timedelta(seconds=1)},
            )
            assert crud_chat.setup_draft(db, draft.id, account_id, user_id) is None
        finally:
            db.query(models.Account).filter_by(id=account_id).delete()
            db.commit()
    engine.dispose()
