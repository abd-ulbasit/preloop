"""Converge the weekly report controls and weekly value cache."""

revision = "20261025_weekly_value_merge"
down_revision = ("20261023_weekly_value_cache", "20261024_weekly_report_merge")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep both independently deployable schema additions."""
    pass


def downgrade() -> None:
    """Leave each schema rollback to its own parent revision."""
    pass
