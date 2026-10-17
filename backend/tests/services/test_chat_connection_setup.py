"""Connection setup uses safe defaults and discovers Mattermost identities."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from preloop.api.endpoints import chat
from preloop.services.chat_providers import (
    ChatProviderError,
    discover_mattermost_bot_user_id,
    MAX_EVENT_BYTES,
)


@pytest.mark.parametrize(
    "provider,label",
    [("slack", "Slack"), ("mattermost", "Mattermost"), ("discord", "Discord")],
)
@pytest.mark.parametrize("name", [None, "", "   ", "  My team  "])
def test_default_name(provider: str, label: str, name: str | None) -> None:
    fields = dict(
        provider=provider,
        workspace_id="team",
        bot_token="secret",
        verification_secret="command",
    )
    if name is not None:
        fields["name"] = name
    payload = chat.ConnectionCreate(**fields)
    assert payload.name == (
        "My team" if name == "  My team  " else f"Preloop {label} bot"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "unauthorized",
        "redirect",
        "bad-body",
        "missing-id",
        "timeout",
        "large",
        "invalid-url",
        "total-timeout",
    ],
)
async def test_discovery_errors_hide_provider_details(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    response = httpx.Response(
        {"unauthorized": 401, "redirect": 302}.get(failure, 200),
        content=b"secret-provider-body" if failure == "bad-body" else b"{}",
    )
    if failure == "large":
        response = httpx.Response(200, content=b"x" * (MAX_EVENT_BYTES + 1))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    if failure in {"timeout", "invalid-url", "total-timeout"}:
        context.__aenter__.side_effect = {
            "timeout": httpx.ReadTimeout("secret-token-in-url"),
            "invalid-url": httpx.InvalidURL("secret-token-in-url"),
            "total-timeout": TimeoutError(),
        }[failure]
    get = MagicMock(return_value=context)
    monkeypatch.setattr(httpx.AsyncClient, "stream", get)
    with pytest.raises(ChatProviderError, match="Check the server address") as error:
        await discover_mattermost_bot_user_id("https://chat.example.com", "bot-secret")
    assert "secret-token" not in str(error.value)
    assert "secret-provider" not in str(error.value)
    assert get.call_args.kwargs["follow_redirects"] is False
    assert get.call_args.kwargs["timeout"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://chat.example.com",
        "https://chat.example.com:abc",
        "https://chat.example.com:99999",
        "https://[broken",
    ],
)
async def test_discovery_validates_url_before_sending_token(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    get = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "stream", get)
    with pytest.raises(ChatProviderError, match="HTTPS origin"):
        await discover_mattermost_bot_user_id(url, "secret")
    get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_id", ["", "manual-bot-id"])
async def test_create_runs_identity_lookup_from_asgi_worker_thread(
    monkeypatch: pytest.MonkeyPatch, manual_id: str
) -> None:
    user = SimpleNamespace(id=uuid4(), account_id=uuid4())
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[chat.get_db_session] = lambda: object()
    app.dependency_overrides[chat.get_current_active_user] = lambda: user
    monkeypatch.setattr(chat, "require_active_account", lambda *args: None)
    monkeypatch.setattr(chat, "ensure_permission_in_oss", lambda *args: None)
    monkeypatch.setattr(chat, "has_permission", lambda *args: True)
    monkeypatch.setattr("preloop.utils.permissions._rbac_checks_enabled", lambda: False)
    monkeypatch.setattr(chat, "encrypt_value", lambda value: value)
    monkeypatch.setattr(
        chat, "public_connection", lambda db, connection, current_user: connection
    )
    create = MagicMock(
        side_effect=lambda db, obj_in: {
            **obj_in,
            "account_id": str(obj_in["account_id"]),
        }
    )
    monkeypatch.setattr(chat.crud_chat.connections, "create", create)
    context = MagicMock()
    context.__aenter__ = AsyncMock(
        return_value=httpx.Response(
            200, json={"id": "detected-bot-id", "username": "preloop"}
        )
    )
    get = MagicMock(return_value=context)
    monkeypatch.setattr(httpx.AsyncClient, "stream", get)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat/connections",
            json={
                "provider": "mattermost",
                "workspace_id": "team",
                "verification_secret": "command-secret",
                "bot_token": "access-secret",
                "base_url": "https://chat.example.com/",
                "bot_user_id": manual_id,
            },
        )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Preloop Mattermost bot"

    credentials = json.loads(create.call_args.kwargs["obj_in"]["credentials_encrypted"])
    assert credentials["bot_user_id"] == (manual_id or "detected-bot-id")
    if manual_id:
        get.assert_not_called()
    else:
        get.assert_called_once_with(
            "GET",
            "https://93.184.216.34/api/v4/users/me",
            headers={
                "Authorization": "Bearer access-secret",
                "Host": "chat.example.com",
            },
            extensions={"sni_hostname": "chat.example.com"},
            timeout=10,
            follow_redirects=False,
        )


@pytest.fixture(autouse=True)
def synthetic_provider_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetic providers never resolve or contact real hosts."""
    import ipaddress

    monkeypatch.setattr(
        "preloop.services.ai_model_provider._resolve_endpoint_host",
        lambda _: [ipaddress.ip_address("93.184.216.34")],
    )
