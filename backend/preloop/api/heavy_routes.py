"""A separate, bounded thread pool for heavy aggregate endpoints (#1593).

Why
---
FastAPI runs every ``def`` handler on one shared anyio thread pool (40
threads) inside one process. Aggregates such as the cost summary, the
gateway usage summary or the audit timeline spend most of their time in
Python (row decoding, grouping, response validation), so they hold the GIL.
When a page fans out ten of them, the light calls the console shell makes on
every page (``/auth/users/me``, ``/me/memberships``, ``/features``, the kill
switch status) wait for the GIL and for a database connection behind them.
That is how a 10 ms membership lookup took 3.1 s on prod.

:func:`heavy_route` moves a handler onto its own ``CapacityLimiter``: at most
``HEAVY_ROUTE_CONCURRENCY`` (default 8) heavy handlers run at once per
process, the rest queue on the limiter without holding a thread, a database
connection or the GIL. Light calls keep the shared pool to themselves.

Usage: put it directly under the route decorator, above any permission
decorator, so the permission check also runs off the event loop::

    @router.get("/summary")
    @heavy_route
    @require_permission("view_cost")
    def get_cost_summary(...): ...
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os
import weakref
from typing import Any, Callable, TypeVar

import anyio
import anyio.to_thread
from sqlalchemy.orm import Session

from preloop.models.db.session import release_transaction

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

DEFAULT_HEAVY_ROUTE_CONCURRENCY = 8

__all__ = [
    "DEFAULT_HEAVY_ROUTE_CONCURRENCY",
    "heavy_route",
    "heavy_route_limiter",
    "heavy_route_concurrency",
]


def heavy_route_concurrency() -> int:
    """Configured number of heavy handlers that may run at once per process."""
    raw = os.getenv("HEAVY_ROUTE_CONCURRENCY")
    if raw is None or not raw.strip():
        return DEFAULT_HEAVY_ROUTE_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid HEAVY_ROUTE_CONCURRENCY=%r; using %d",
            raw,
            DEFAULT_HEAVY_ROUTE_CONCURRENCY,
        )
        return DEFAULT_HEAVY_ROUTE_CONCURRENCY
    return max(1, value)


_limiters: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, anyio.CapacityLimiter]" = weakref.WeakKeyDictionary()


def heavy_route_limiter() -> anyio.CapacityLimiter:
    """The limiter for the running event loop.

    One per loop rather than one per process: the API serves from a single
    loop, but test clients each start their own, and a limiter must not be
    shared across loops.
    """
    loop = asyncio.get_running_loop()
    limiter = _limiters.get(loop)
    if limiter is None:
        limiter = anyio.CapacityLimiter(heavy_route_concurrency())
        _limiters[loop] = limiter
    return limiter


def _release_all(sessions: list[Session]) -> None:
    for session in sessions:
        release_transaction(session)


def heavy_route(func: F) -> F:
    """Run a synchronous handler on the bounded heavy-route pool.

    The returned coroutine function keeps ``func``'s signature (through
    ``functools.wraps``), so FastAPI resolves the same parameters and
    dependencies. Dependencies still resolve as before; only the handler body
    waits for a heavy slot.

    Raises:
        TypeError: when ``func`` is already a coroutine function. An ``async``
            handler runs on the event loop; it should keep its blocking work
            in :func:`preloop.api.loop_safety.run_db_off_loop` instead.
    """
    if inspect.iscoroutinefunction(func):
        raise TypeError(
            f"heavy_route expects a synchronous handler, got async {func.__name__}"
        )

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        sessions = [
            value
            for value in (*args, *kwargs.values())
            if isinstance(value, Session) and value.in_transaction()
        ]
        if sessions:
            # The auth dependencies already queried through the request
            # session, so it holds a pooled connection. Hand it back before
            # queueing for a heavy slot; the handler checks one out again
            # when it runs. Off the loop, because COMMIT is a round trip.
            await anyio.to_thread.run_sync(_release_all, sessions)
        return await anyio.to_thread.run_sync(
            functools.partial(func, *args, **kwargs),
            limiter=heavy_route_limiter(),
        )

    wrapper.__heavy_route__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]
