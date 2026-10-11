"""Run the audit outbox migration's downgrade/upgrade on real Postgres."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from preloop.models.models import AuditOutbox

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "preloop"
    / "models"
    / "alembic"
    / "versions"
    / "20261011_audit_outbox.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("audit_outbox_mig", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_downgrade_then_upgrade_rebuilds_the_orm_shape(db_session):
    migration = _load_migration()
    context = MigrationContext.configure(db_session.connection())
    with Operations.context(context):
        migration.downgrade()
    assert not inspect(db_session.connection()).has_table("audit_outbox")
    with Operations.context(context):
        migration.upgrade()
    inspector = inspect(db_session.connection())
    columns = {c["name"] for c in inspector.get_columns("audit_outbox")}
    assert columns == {c.name for c in AuditOutbox.__table__.columns}
    indexes = {i["name"] for i in inspector.get_indexes("audit_outbox")}
    assert {"ix_audit_outbox_due", "ix_audit_outbox_account_id"} <= indexes
