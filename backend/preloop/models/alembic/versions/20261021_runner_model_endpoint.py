"""Add account-owned runner model endpoint identities.

Revision ID: 20261021_runner_models
Revises: 20261020_jira_callback
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261021_runner_models"
down_revision = "20261020_jira_callback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep existing endpoints direct and bind optional local model runners."""
    op.add_column(
        "ai_model",
        sa.Column(
            "endpoint_type", sa.String(16), nullable=False, server_default="direct"
        ),
    )
    op.add_column(
        "ai_model", sa.Column("runner_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_ai_model_runner_id",
        "ai_model",
        "flow_runner",
        ["runner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_ai_model_runner_id", "ai_model", ["runner_id"])
    op.create_check_constraint(
        "ck_ai_model_endpoint_type", "ai_model", "endpoint_type IN ('direct', 'runner')"
    )


def downgrade() -> None:
    """Remove runner routing fields."""
    op.drop_constraint("ck_ai_model_endpoint_type", "ai_model", type_="check")
    op.drop_index("ix_ai_model_runner_id", table_name="ai_model")
    op.drop_constraint("fk_ai_model_runner_id", "ai_model", type_="foreignkey")
    op.drop_column("ai_model", "runner_id")
    op.drop_column("ai_model", "endpoint_type")
