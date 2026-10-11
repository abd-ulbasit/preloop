"""Add the durable audit outbox table (EE #94); additive, no backfill.

Revises ``20261011_flow_result_schema`` so runner presence and the flow
result schema stay on the single head.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261011_audit_outbox"
down_revision = "20261011_flow_result_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``audit_outbox``; empty until the EE flag turns the relay on."""
    op.create_table(
        "audit_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "seq",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
            unique=True,
        ),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("priority", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_audit_outbox_id", "audit_outbox", ["id"])
    op.create_index(
        "ix_audit_outbox_due", "audit_outbox", ["status", "next_attempt_at"]
    )
    op.create_index("ix_audit_outbox_account_id", "audit_outbox", ["account_id"])


def downgrade() -> None:
    """Drop the outbox; undelivered intents are lost, as with any downgrade."""
    op.drop_index("ix_audit_outbox_account_id", table_name="audit_outbox")
    op.drop_index("ix_audit_outbox_due", table_name="audit_outbox")
    op.drop_index("ix_audit_outbox_id", table_name="audit_outbox")
    op.drop_table("audit_outbox")
