"""Add tenant-local expected-device roster storage.

Revision ID: 20261014_expected_devices
Revises: 20261014_tool_config_scope_nulls
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261014_expected_devices"
down_revision = "20261014_tool_config_scope_nulls"
branch_labels = None
depends_on = None
_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS


def upgrade() -> None:
    """Create roster entries separately from observations and credentials."""
    op.create_table(
        "expected_device",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_ref_hash", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("attributes", JSONB(), nullable=False),
        sa.Column("last_reported_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "account_id", "device_ref_hash", name="uq_expected_device_ref"
        ),
    )
    op.create_index("ix_expected_device_id", "expected_device", ["id"])
    op.create_index("ix_expected_device_account_id", "expected_device", ["account_id"])


def downgrade() -> None:
    """Remove roster storage only."""
    op.drop_table("expected_device")
