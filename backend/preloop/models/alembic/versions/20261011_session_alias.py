"""Add nullable spawn-time session aliases (#1552)."""

import sqlalchemy as sa
from alembic import op

revision = "20261011_session_alias"
down_revision = "20261013_note_receipts"
branch_labels = None
depends_on = None
_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)


def upgrade() -> None:
    op.add_column("runtime_session", sa.Column("alias", sa.String(64), nullable=True))
    op.add_column(
        "runtime_session", sa.Column("alias_namespace", sa.String(320), nullable=True)
    )
    op.create_index(
        "uq_runtime_session_active_alias",
        "runtime_session",
        ["account_id", "alias_namespace", "alias"],
        unique=True,
        postgresql_where=sa.text("alias IS NOT NULL AND ended_at IS NULL"),
    )
    op.create_index("ix_runtime_session_alias", "runtime_session", ["alias"])


def downgrade() -> None:
    op.drop_index("uq_runtime_session_active_alias", table_name="runtime_session")
    op.drop_column("runtime_session", "alias_namespace")
    op.drop_index("ix_runtime_session_alias", table_name="runtime_session")
    op.drop_column("runtime_session", "alias")
