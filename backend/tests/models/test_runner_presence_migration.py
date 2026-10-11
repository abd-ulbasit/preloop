"""Migration 20261011_runner_presence upgrades and downgrades cleanly."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.orm import Session

COLUMNS = {"sleep_signaled_at", "os_idle_seconds", "presence_state", "presence_since"}


def _load():
    path = (
        Path(__file__).resolve().parents[2]
        / "preloop/models/alembic/versions/20261011_runner_presence.py"
    )
    spec = importlib.util.spec_from_file_location("runner_presence_mig", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(db_session: Session) -> set:
    return {
        c["name"] for c in inspect(db_session.connection()).get_columns("flow_runner")
    }


def test_runner_presence_migration_down_and_up(db_session: Session) -> None:
    migration = _load()
    assert COLUMNS <= _columns(db_session)
    with Operations.context(MigrationContext.configure(db_session.connection())):
        migration.downgrade()
        assert not COLUMNS & _columns(db_session)
        migration.upgrade()
    assert COLUMNS <= _columns(db_session)
