"""api_usage carries per-table autovacuum thresholds (#1583).

The Spend reads depend on index-only scans of the newest rows, which need
the visibility map those vacuums maintain.
"""

import importlib

from sqlalchemy import text

migration = importlib.import_module(
    "preloop.models.alembic.versions.20261015_api_usage_autovacuum"
)


def _reloptions(db_session) -> dict[str, str]:
    raw = db_session.execute(
        text("SELECT reloptions FROM pg_class WHERE relname = 'api_usage'")
    ).scalar()
    return dict(option.split("=", 1) for option in raw or [])


def test_migrated_table_has_the_thresholds(db_session):
    options = _reloptions(db_session)
    for name, value in migration.SETTINGS.items():
        assert options.get(name) == value, name


def test_thresholds_trigger_well_before_the_server_default():
    """The point of the migration: about 1% new rows, not 20%."""
    assert float(migration.SETTINGS["autovacuum_vacuum_insert_scale_factor"]) <= 0.02
    assert float(migration.SETTINGS["autovacuum_vacuum_scale_factor"]) <= 0.05
