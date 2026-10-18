"""Bound concurrent-index waits separately from blocking schema changes.

PostgreSQL concurrent index operations wait for existing writers and snapshots
without stopping ordinary reads or writes. The migration session's short DDL
lock timeout cancels those waits and can leave an invalid index to rebuild.
Only explicit concurrent-index blocks get this longer, finite budget. The
original session settings are restored even when an operation fails.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import sqlalchemy as sa
from alembic.migration import MigrationContext

DEFAULT_LOCK_TIMEOUT_SECONDS = 60
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 300


def _seconds(name: str, default: int, maximum: int) -> int:
    """Read a positive, bounded integer; unsafe/invalid overrides use defaults."""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if 0 < value <= maximum else default


@contextmanager
def concurrent_index_ddl(context: MigrationContext) -> Iterator[None]:
    """Run only concurrent index statements with bounded PostgreSQL timeouts.

    Args:
        context: The active Alembic context. Entering its autocommit block is
            required by PostgreSQL's concurrent index commands.
    """
    with context.autocommit_block():
        if context.dialect.name != "postgresql":
            yield
            return
        connection = context.bind
        assert connection is not None
        previous_lock, previous_statement = connection.execute(
            sa.text(
                "SELECT current_setting('lock_timeout'), "
                "current_setting('statement_timeout')"
            )
        ).one()
        set_timeouts = sa.text(
            "SELECT set_config('lock_timeout', :lock_timeout, false), "
            "set_config('statement_timeout', :statement_timeout, false)"
        )
        lock_seconds = _seconds(
            "PRELOOP_MIGRATION_CONCURRENT_LOCK_TIMEOUT_SECONDS",
            DEFAULT_LOCK_TIMEOUT_SECONDS,
            300,
        )
        statement_seconds = _seconds(
            "PRELOOP_MIGRATION_CONCURRENT_STATEMENT_TIMEOUT_SECONDS",
            DEFAULT_STATEMENT_TIMEOUT_SECONDS,
            900,
        )
        try:
            connection.execute(
                set_timeouts,
                {
                    "lock_timeout": f"{lock_seconds}s",
                    "statement_timeout": f"{statement_seconds}s",
                },
            )
            yield
        finally:
            connection.execute(
                set_timeouts,
                {
                    "lock_timeout": previous_lock,
                    "statement_timeout": previous_statement,
                },
            )
