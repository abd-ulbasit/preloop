"""Add inert MCP inventory review persistence.

Revision ID: 20261021_mcp_inventory
Revises: 20261020_jira_callback
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261021_mcp_inventory"
down_revision = "20261020_jira_callback"
branch_labels = None
depends_on = None


def _columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "server_id",
            UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("registry_revision", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    """Create storage without registering any commercial API."""
    op.create_table(
        "mcp_inventory_review",
        *_columns(),
        sa.Column("approval_state", sa.String(16), nullable=False),
        sa.UniqueConstraint(
            "account_id",
            "server_id",
            "registry_revision",
            name="uq_mcp_inventory_revision",
        ),
        sa.CheckConstraint("approval_state IN ('approved', 'unknown', 'unapproved')"),
    )
    op.create_table(
        "mcp_inventory_finding",
        *_columns(),
        sa.Column("evidence_class", sa.String(32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ignored_until", sa.DateTime(timezone=True)),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("managed_agent.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "person_id",
            UUID(as_uuid=True),
            sa.ForeignKey("person.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint(
            "account_id",
            "server_id",
            "registry_revision",
            "evidence_class",
            name="uq_mcp_inventory_finding",
        ),
        sa.CheckConstraint("evidence_class IN ('configured_route', 'observed_call')"),
    )
    for table in ("mcp_inventory_review", "mcp_inventory_finding"):
        op.create_index(f"ix_{table}_id", table, ["id"])
        op.create_index(f"ix_{table}_account_id", table, ["account_id"])


def downgrade() -> None:
    """Remove optional inventory storage."""
    op.drop_table("mcp_inventory_finding")
    op.drop_table("mcp_inventory_review")
