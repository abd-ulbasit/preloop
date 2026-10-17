"""Merge the published device fleet and runner model migration branches."""

revision = "20261021_fleet_models_merge"
down_revision = ("20261021_device_fleet", "20261021_runner_models")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join both independent schema changes without additional DDL."""


def downgrade() -> None:
    """Leave schema rollback to each parent revision."""
