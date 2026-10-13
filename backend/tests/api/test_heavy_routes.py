"""The heavy-route pool keeps light console calls fast under aggregates (#1593)."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from preloop.api import heavy_routes
from preloop.api.heavy_routes import heavy_route


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch: pytest.MonkeyPatch):
    """Each test builds its limiter from its own environment."""
    heavy_routes._limiters.clear()
    yield
    heavy_routes._limiters.clear()


def test_concurrency_defaults_and_rejects_garbage(monkeypatch):
    monkeypatch.delenv("HEAVY_ROUTE_CONCURRENCY", raising=False)
    assert heavy_routes.heavy_route_concurrency() == 6
    monkeypatch.setenv("HEAVY_ROUTE_CONCURRENCY", "3")
    assert heavy_routes.heavy_route_concurrency() == 3
    monkeypatch.setenv("HEAVY_ROUTE_CONCURRENCY", "0")
    assert heavy_routes.heavy_route_concurrency() == 1
    monkeypatch.setenv("HEAVY_ROUTE_CONCURRENCY", "many")
    assert heavy_routes.heavy_route_concurrency() == 6


def test_rejects_async_handlers():
    async def handler() -> None:
        return None

    with pytest.raises(TypeError):
        heavy_route(handler)


def test_keeps_the_signature_fastapi_reads():
    def handler(a: int, b: str = "x") -> dict:
        return {"a": a, "b": b}

    wrapped = heavy_route(handler)
    assert inspect.iscoroutinefunction(wrapped)
    assert list(inspect.signature(wrapped).parameters) == ["a", "b"]
    assert wrapped.__heavy_route__ is True


@pytest.mark.asyncio
async def test_runs_at_most_the_configured_number_at_once(monkeypatch):
    monkeypatch.setenv("HEAVY_ROUTE_CONCURRENCY", "2")
    lock = threading.Lock()
    running = 0
    peak = 0

    def handler(i: int) -> int:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return i

    wrapped = heavy_route(handler)
    results = await asyncio.gather(*(wrapped(i) for i in range(8)))
    assert results == list(range(8))
    assert peak == 2


@pytest.mark.asyncio
async def test_queued_handlers_leave_the_loop_free(monkeypatch):
    """While heavy handlers fill and queue, a light coroutine still runs at once."""
    monkeypatch.setenv("HEAVY_ROUTE_CONCURRENCY", "1")
    started = threading.Event()
    release = threading.Event()

    def handler() -> str:
        started.set()
        release.wait(5)
        return "done"

    wrapped = heavy_route(handler)
    tasks = [asyncio.create_task(wrapped()) for _ in range(5)]
    await asyncio.to_thread(started.wait, 5)
    t0 = time.perf_counter()
    await asyncio.sleep(0)
    assert time.perf_counter() - t0 < 0.05
    release.set()
    assert await asyncio.gather(*tasks) == ["done"] * 5


@pytest.mark.asyncio
async def test_hands_back_the_request_connection_before_queueing():
    """A session the auth dependency already used is released, then reused."""
    db = MagicMock(spec=Session)
    db.in_transaction.return_value = True
    seen: list[str] = []
    db.commit.side_effect = lambda: seen.append("commit")

    def handler(db: Session) -> str:
        seen.append("handler")
        return "ok"

    assert await heavy_route(handler)(db=db) == "ok"
    assert seen == ["commit", "handler"]


@pytest.mark.asyncio
async def test_idle_session_is_left_alone():
    db = MagicMock(spec=Session)
    db.in_transaction.return_value = False

    def handler(db: Session) -> str:
        return "ok"

    assert await heavy_route(handler)(db=db) == "ok"
    db.commit.assert_not_called()


def test_works_as_a_fastapi_route_with_dependencies():
    app = FastAPI()

    def dep() -> str:
        return "dep"

    @app.get("/heavy")
    @heavy_route
    def handler(q: int = 1, value: str = Depends(dep)) -> dict:
        return {"q": q, "value": value, "thread": threading.current_thread().name}

    with TestClient(app) as client:
        body = client.get("/heavy", params={"q": 3}).json()
    assert body["q"] == 3
    assert body["value"] == "dep"
    assert body["thread"] != threading.main_thread().name


def test_console_aggregates_use_the_heavy_pool():
    """The aggregates measured in round 3 stay on the bounded pool."""
    from preloop.api.endpoints import account, ai_models, audit_chain, cost, tools

    for handler in (
        cost.get_cost_summary,
        cost.get_cost_timeseries,
        account.get_account_gateway_usage_summary,
        account.get_account_rate_limit_report,
        ai_models.get_ai_models_overview,
        ai_models.list_ai_model_runtime_sessions,
        tools.get_tool_usage_stats,
        audit_chain.get_chain_status,
    ):
        assert getattr(handler, "__heavy_route__", False), handler.__name__
