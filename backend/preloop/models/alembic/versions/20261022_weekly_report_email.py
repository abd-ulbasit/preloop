"""Account and member switches for the weekly report email."""

from alembic import op
import sqlalchemy as sa

revision = "20261022_weekly_report_email"
down_revision = "20261021_mcp_fleet_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve current delivery unless someone opts out."""
    for table in ("account", "notification_preferences"):
        op.add_column(
            table,
            sa.Column(
                "weekly_report_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    """Remove the report-specific switches."""
    for table in ("notification_preferences", "account"):
        op.drop_column(table, "weekly_report_enabled")
