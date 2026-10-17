"""Bounded, pull-driven model transport over an authenticated runner socket."""

from __future__ import annotations

import asyncio
import json
import hmac
import threading
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import nats
from nats.aio.msg import Msg
from fastapi import WebSocket
from litellm import ModelResponse
from litellm.types.utils import ModelResponseStream

from preloop.config import settings
from preloop.services.model_gateway_errors import ModelGatewayAPIError

MAX_REQUEST_BYTES = 1024 * 1024
MAX_FRAME_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ACTIVE_REQUESTS = 4
IDLE_SECONDS = 60
TOTAL_SECONDS = 600


def normalize_local_base_url(value: str) -> str:
    """Accept only explicit HTTP loopback OpenAI-compatible base URLs."""
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == "http"
            and url.hostname in {"localhost", "127.0.0.1", "::1"}
            and url.port is not None
            and 0 < url.port <= 65535
            and url.username is None
            and url.password is None
            and url.path in {"/v1", "/v1/"}
            and not url.query
            and not url.fragment
            and "?" not in value
            and "#" not in value
            and not any(char.isspace() for char in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Runner endpoint must be http://loopback:port/v1")
    return value.rstrip("/")


def runner_model_subject(account_id: str, runner_id: str) -> str:
    """Build an internal subject from trusted database UUIDs."""
    return f"preloop.runner_models.{account_id}.{runner_id}"


class RunnerModelConnection:
    """Bridge one socket to internal NATS RPC with one frame in flight per call."""

    def __init__(
        self,
        websocket: WebSocket,
        account_id: str,
        runner_id: str,
        *,
        credential_hash: str | None = None,
        connection_id: str | None = None,
    ) -> None:
        self.websocket = websocket
        self.credential_hash = credential_hash
        self.connection_id = connection_id
        self.account_id = account_id
        self.runner_id = runner_id
        self.subject = runner_model_subject(account_id, runner_id)
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.messages: dict[str, Msg] = {}
        self.closed = False
        self.active: dict[str, float] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.nc: Any = None
        self.subscription: Any = None

    async def start(self) -> None:
        """Subscribe only while this runner has an authenticated live socket."""
        self.nc = await nats.connect(
            settings.nats_url,
            connect_timeout=2,
            max_reconnect_attempts=0,
            allow_reconnect=False,
        )

        async def receive(message: Msg) -> None:
            task = asyncio.create_task(self._request(message))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        self.subscription = await self.nc.subscribe(self.subject, cb=receive)
        await self.nc.flush()

    async def _request(self, message: Msg) -> None:
        request_id = ""
        owned = False
        try:
            raw = json.loads(message.data)
            request_id = str(raw["request_id"])
            operation = raw.get("operation")
            if self.closed:
                await message.respond(
                    b'{"status":503,"error":"runner_offline","done":true}'
                )
                return
            now = time.monotonic()
            for key, started in list(self.active.items()):
                if now - started > TOTAL_SECONDS:
                    self.active.pop(key, None)
            if operation == "cancel":
                self.active.pop(request_id, None)
                await self.websocket.send_json(raw)
                if message.reply:
                    await message.respond(b'{"done":true}')
                return
            if request_id in self.messages:
                await message.respond(
                    b'{"status":409,"error":"runner_proxy_error","done":true}'
                )
                return
            self.messages[request_id] = message
            owned = True
            if operation == "start":
                if len(self.active) >= MAX_ACTIVE_REQUESTS:
                    await message.respond(
                        b'{"status":429,"error":"runner_busy","done":true}'
                    )
                    return
                if len(message.data) > MAX_REQUEST_BYTES:
                    raise ValueError("request too large")
                normalize_local_base_url(raw["base_url"])
                # Reserve before yielding to the database worker, so concurrent
                # authorizations cannot all observe the same available slot.
                self.active[request_id] = now
                if not await asyncio.to_thread(self._authorized, raw):
                    raise ValueError("unregistered model")
            elif operation != "next" or request_id not in self.active:
                raise ValueError("unknown request")
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = future
            await self.websocket.send_json(raw)
            reply = await asyncio.wait_for(future, IDLE_SECONDS)
            if reply.get("done") or reply.get("error"):
                self.active.pop(request_id, None)
            await message.respond(json.dumps(reply).encode())
        except (ValueError, KeyError, TypeError):
            self.active.pop(request_id, None)
            await message.respond(
                b'{"status":403,"error":"runner_model_not_allowed","done":true}'
            )
        except TimeoutError:
            self.active.pop(request_id, None)
            await message.respond(
                b'{"status":504,"error":"runner_proxy_timeout","done":true}'
            )
        except Exception:
            self.active.pop(request_id, None)
            await message.respond(
                b'{"status":503,"error":"runner_offline","done":true}'
            )
        finally:
            if owned:
                self.pending.pop(request_id, None)
                self.messages.pop(request_id, None)

    def _authorized(self, raw: dict[str, Any]) -> bool:
        """Recheck stored model/account binding before handing work to the host."""
        from preloop.models.crud import crud_ai_model
        from preloop.models.crud.flow_runner import crud_flow_runner
        from preloop.models.db.session import get_db_session

        for db in get_db_session():
            runner = crud_flow_runner.get(
                db, id=self.runner_id, account_id=self.account_id
            )
            if (
                not runner
                or not self.credential_hash
                or not self.connection_id
                or not hmac.compare_digest(
                    runner.token_hash or "", self.credential_hash
                )
                or (runner.publication_capabilities or {}).get("connection_id")
                != self.connection_id
            ):
                raise ConnectionError("Runner connection is no longer current")
            model = crud_ai_model.get(
                db, id=raw.get("model_id"), account_id=self.account_id
            )
            return bool(
                model
                and model.endpoint_type == "runner"
                and str(model.runner_id) == self.runner_id
                and model.api_endpoint == raw.get("base_url")
                and model.model_identifier == (raw.get("body") or {}).get("model")
            )
        return False

    def response(self, raw: dict[str, Any]) -> None:
        """Accept a reply only on the socket that owns its pending request."""
        future = self.pending.get(str(raw.get("request_id")))
        if future is None or future.done():
            return
        if (
            type(raw.get("status", 200)) is not int
            or not 200 <= raw.get("status", 200) <= 599
            or not isinstance(raw.get("data", ""), str)
            or type(raw.get("done", False)) is not bool
            or (
                raw.get("error") is not None
                and (not isinstance(raw["error"], str) or len(raw["error"]) > 128)
            )
        ):
            future.set_result(
                {"status": 502, "error": "runner_proxy_error", "done": True}
            )
        elif len(json.dumps(raw).encode()) > MAX_FRAME_BYTES + 2048:
            future.set_result(
                {"status": 502, "error": "runner_frame_too_large", "done": True}
            )
        else:
            future.set_result(raw)

    async def close(self) -> None:
        """Fail RPCs and cancel authorizations before closing the subscription."""
        if self.closed:
            return
        self.closed = True
        for task in self.tasks:
            task.cancel()
        for future in self.pending.values():
            if not future.done():
                future.set_result(
                    {"status": 503, "error": "runner_offline", "done": True}
                )
        for message in list(self.messages.values()):
            try:
                await message.respond(
                    b'{"status":503,"error":"runner_offline","done":true}'
                )
            except Exception:
                pass
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.active.clear()
        if self.nc is not None:
            await self.nc.close()


class RunnerModelStream:
    """Synchronous gateway iterator with a private NATS I/O event loop."""

    def __init__(self, model: Any, body: dict[str, Any]) -> None:
        self.subject = runner_model_subject(str(model.account_id), str(model.runner_id))
        self.request_id = str(uuid4())
        self.start_frame = {
            "type": "model_request",
            "operation": "start",
            "request_id": self.request_id,
            "model_id": str(model.id),
            "base_url": model.api_endpoint,
            "body": body,
        }
        if len(json.dumps(self.start_frame).encode()) > MAX_REQUEST_BYTES:
            raise ModelGatewayAPIError(
                provider="openai",
                status_code=413,
                message="Runner model request exceeds 1 MiB",
            )
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread_started = False
        self.nc: Any = None
        self.started = time.monotonic()
        self.total_bytes = 0
        self.closed = False
        self.first = True

    async def _rpc(self, frame: dict[str, Any]) -> dict[str, Any]:
        if self.nc is None:
            self.nc = await nats.connect(
                settings.nats_url,
                connect_timeout=2,
                max_reconnect_attempts=0,
                allow_reconnect=False,
            )
        reply = await self.nc.request(
            self.subject, json.dumps(frame).encode(), timeout=IDLE_SECONDS + 2
        )
        return json.loads(reply.data)

    def read(self) -> dict[str, Any]:
        """Pull one bounded frame, mapping broker failures to gateway errors."""
        if time.monotonic() - self.started > TOTAL_SECONDS:
            self.close()
            raise ModelGatewayAPIError(
                provider="openai",
                status_code=504,
                message="Runner model deadline exceeded",
            )
        frame = (
            self.start_frame
            if self.first
            else {
                "type": "model_request",
                "operation": "next",
                "request_id": self.request_id,
            }
        )
        self.first = False
        if not self.thread_started:
            self.thread.start()
            self.thread_started = True
        future = None
        try:
            future = asyncio.run_coroutine_threadsafe(self._rpc(frame), self.loop)
            reply = future.result(timeout=IDLE_SECONDS + 5)
        except Exception as exc:
            if future is not None:
                future.cancel()
            self.close()
            raise ModelGatewayAPIError(
                provider="openai",
                status_code=503,
                code="runner_offline",
                message="Runner is offline or unavailable",
            ) from exc
        self.total_bytes += len(json.dumps(reply).encode())
        if self.total_bytes > MAX_RESPONSE_BYTES:
            self.close()
            raise ModelGatewayAPIError(
                provider="openai",
                status_code=502,
                message="Runner model response exceeds limit",
            )
        status = reply.get("status", 200)
        if type(status) is not int or not 200 <= status <= 599:
            status = 502
        if reply.get("error") or status >= 300:
            code = reply.get("error")
            if code not in {
                "runner_offline",
                "runner_busy",
                "runner_model_not_allowed",
                "runner_proxy_timeout",
                "runner_frame_too_large",
                "runner_response_limit",
                "runner_upstream_unavailable",
                "runner_upstream_read_error",
            }:
                code = "runner_upstream_error"
            self.close()
            raise ModelGatewayAPIError(
                provider="openai",
                status_code=status if status >= 400 else 502,
                code=code,
                message=(
                    "Runner is offline or unavailable"
                    if code == "runner_offline"
                    else "Runner model request failed: " + code
                ),
            )
        return reply

    def __iter__(self) -> RunnerModelStream:
        """Return this closeable, lazy stream without allocating a worker."""
        return self

    def __next__(self) -> ModelResponseStream:
        """Parse one OpenAI SSE event without accumulating the generation."""
        try:
            while not self.closed:
                reply = self.read()
                line = reply.get("data", "")
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        return ModelResponseStream(**json.loads(data))
                    except (ValueError, TypeError) as exc:
                        raise ModelGatewayAPIError(
                            provider="openai",
                            status_code=502,
                            message="Runner returned an invalid model response",
                        ) from exc
                if reply.get("done"):
                    break
        except BaseException:
            self.close()
            raise
        self.close()
        raise StopIteration

    def close(self) -> None:
        """Cancel upstream work and shut down this call's private I/O thread."""
        if self.closed:
            return
        self.closed = True
        if not self.thread_started:
            self.loop.close()
            return

        async def cleanup() -> None:
            if self.nc is not None:
                try:
                    await self.nc.publish(
                        self.subject,
                        json.dumps(
                            {
                                "type": "model_request",
                                "operation": "cancel",
                                "request_id": self.request_id,
                            }
                        ).encode(),
                    )
                    await self.nc.flush(timeout=1)
                finally:
                    await self.nc.close()

        try:
            asyncio.run_coroutine_threadsafe(cleanup(), self.loop).result(timeout=3)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=3)
            if not self.thread.is_alive():
                self.loop.close()


def runner_completion(model: Any, body: dict[str, Any], *, stream: bool) -> Any:
    """Dispatch only chat completion provider I/O through the runner tunnel."""
    tunnel = RunnerModelStream(model, body)
    if stream:
        return tunnel
    try:
        reply = tunnel.read()
        try:
            return ModelResponse(**json.loads(reply["data"]))
        except (ValueError, TypeError) as exc:
            raise ModelGatewayAPIError(
                provider="openai",
                status_code=502,
                message="Runner returned an invalid model response",
            ) from exc
    finally:
        tunnel.close()
