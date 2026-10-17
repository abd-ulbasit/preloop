"""Bounded Slack OAuth exchange and replica-owned refresh-token rotation."""

from __future__ import annotations
import asyncio
import json
import os
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session
from preloop.models import models
from preloop.models.crud import crud_chat
from preloop.services.chat_providers import ChatProviderError
from preloop.utils.encryption import decrypt_value, encrypt_value


async def exchange_slack(fields: dict[str, str]) -> dict[str, Any]:
    """Secrets go only to Slack's fixed TLS endpoint, never through redirects."""
    try:
        async with asyncio.timeout(12), httpx.AsyncClient(trust_env=False) as client:
            async with client.stream(
                "POST",
                "https://slack.com/api/oauth.v2.access",
                data={
                    **fields,
                    "client_id": os.environ["CHAT_SLACK_CLIENT_ID"],
                    "client_secret": os.environ["CHAT_SLACK_CLIENT_SECRET"],
                },
                timeout=10,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ChatProviderError("Slack authorization failed")
                raw = bytearray()
                async for chunk in response.aiter_bytes(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ChatProviderError(
                            "Slack authorization response is too large"
                        )
                data = json.loads(bytes(raw))
                if (
                    not isinstance(data, dict)
                    or data.get("ok") is not True
                    or not isinstance(data.get("access_token"), str)
                ):
                    raise ChatProviderError("Slack authorization failed; start again")
                return data
    except (httpx.HTTPError, ValueError, TimeoutError, KeyError) as exc:
        raise ChatProviderError(
            "Slack authorization failed; check app configuration and start again"
        ) from exc


async def refresh_slack_connection(
    db: Session, connection: models.ChatConnection, listener_token: str
) -> bool:
    """Only the connection's leased worker rotates its encrypted refresh token."""
    old_ciphertext = connection.credentials_encrypted
    state = json.loads(decrypt_value(old_ciphertext))
    if (
        not state.get("slack_refresh_token")
        or float(state.get("slack_token_expires_at", 0)) > time.time() + 300
    ):
        return True
    connection_id = connection.id
    # Release the read transaction before the token endpoint call.
    db.commit()
    try:
        data = await exchange_slack(
            {
                "grant_type": "refresh_token",
                "refresh_token": state["slack_refresh_token"],
            }
        )
        if not data.get("refresh_token") or not isinstance(data.get("expires_in"), int):
            raise ChatProviderError("Slack did not return rotated credentials")
        state.update(
            bot_token=data["access_token"],
            slack_refresh_token=data["refresh_token"],
            slack_token_expires_at=time.time() + data["expires_in"],
        )
        return crud_chat.rotate_credentials(
            db,
            connection_id,
            listener_token,
            old_ciphertext,
            encrypt_value(json.dumps(state)),
        )
    except ChatProviderError:
        crud_chat.listener_health(
            db,
            connection_id,
            listener_token,
            "reconnecting",
            last_error="Slack token refresh failed; reinstall if authorization was revoked",
        )
        return False
