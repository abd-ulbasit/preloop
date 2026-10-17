"""Bound JSON setup/support requests before FastAPI allocates their bodies."""

from __future__ import annotations
import asyncio
from collections.abc import Awaitable, Callable
from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute


class BoundedChatRoute(APIRoute):
    """Small interactive APIs accept at most 32 KiB within 15 seconds."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method in {"POST", "PUT", "PATCH"}:
                raw = bytearray()
                try:
                    async with asyncio.timeout(15):
                        async for chunk in request.stream():
                            raw.extend(chunk)
                            if len(raw) > 32768:
                                raise HTTPException(413, "Request is too large")
                except TimeoutError as exc:
                    raise HTTPException(408, "Request body timed out") from exc
                request._body = bytes(raw)
            return await original(request)

        return bounded
