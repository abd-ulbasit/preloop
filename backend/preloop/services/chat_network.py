"""Pin tenant-configured Mattermost HTTPS connections to policy-approved DNS."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
from typing import Any

from preloop.services.chat_providers import ChatProviderError, validate_mattermost_url


async def pinned_mattermost_origin(origin: str) -> tuple[str, str, str]:
    """Reuse instance outbound CIDR policy and preserve TLS SNI/Host.

    The HTTP/WebSocket clients dial the approved numeric IP rather than resolve
    the hostname again. MODEL_ENDPOINT_ALLOWED_CIDRS opts private intranet ranges
    in; metadata/link-local addresses remain denied even with that allowance.
    """
    from preloop.services.ai_model_provider import (
        _resolve_endpoint_host,
        validate_gateway_api_endpoint,
    )

    origin = validate_mattermost_url(origin)
    parsed = urlsplit(origin)
    hostname = parsed.hostname or ""
    try:
        async with asyncio.timeout(10):
            addresses = await asyncio.to_thread(_resolve_endpoint_host, hostname)
        if not addresses:
            raise ChatProviderError("Mattermost hostname could not be resolved")
        validate_gateway_api_endpoint(origin, resolve=lambda _: addresses)
    except ValueError as exc:
        raise ChatProviderError(
            "Mattermost destination is unavailable or blocked by the instance outbound network policy. Administrators can allow private intranet CIDRs with MODEL_ENDPOINT_ALLOWED_CIDRS."
        ) from exc
    ip = str(addresses[0])
    authority = f"[{ip}]" if ":" in ip else ip
    if parsed.port:
        authority += f":{parsed.port}"
    return "https://" + authority, parsed.netloc, hostname


async def mattermost_websocket_options(origin: str) -> dict:
    """Pin the socket without depending on the client's proxy API version."""
    import inspect
    from websockets.asyncio.client import connect

    numeric, _, hostname = await pinned_mattermost_origin(origin)
    options: dict = {"host": urlsplit(numeric).hostname, "server_hostname": hostname}
    if "proxy" in inspect.signature(connect).parameters:
        options["proxy"] = None
    return options


async def authenticate_mattermost_socket(websocket: Any, token: str) -> None:
    """The hello event may precede the challenge acknowledgement."""
    import json

    async with asyncio.timeout(10):
        await websocket.send(
            json.dumps(
                {
                    "seq": 1,
                    "action": "authentication_challenge",
                    "data": {"token": token},
                }
            )
        )
        for _ in range(4):
            frame = json.loads(await websocket.recv())
            if isinstance(frame, dict) and frame.get("seq_reply") == 1:
                if frame.get("status") == "OK":
                    return
                raise ChatProviderError("Mattermost authentication failed")
    raise ChatProviderError("Mattermost authentication acknowledgement missing")
