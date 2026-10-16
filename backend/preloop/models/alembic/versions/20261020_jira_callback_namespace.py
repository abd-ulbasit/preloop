"""Retain verified Jira callback namespaces independently of live grants.

Revision ID: 20261020_jira_callback
Revises: 20261019_gov_thresholds
"""

from alembic import op
import sqlalchemy as sa

revision = "20261020_jira_callback"
down_revision = "20261019_gov_thresholds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable namespace proof; successful registration/refresh fills it."""
    op.add_column("webhook", sa.Column("jira_cloud_id", sa.String(255), nullable=True))
    op.add_column(
        "webhook", sa.Column("jira_provider_subject", sa.String(1000), nullable=True)
    )


def downgrade() -> None:
    """Remove retained callback namespace metadata."""
    op.drop_column("webhook", "jira_provider_subject")
    op.drop_column("webhook", "jira_cloud_id")
