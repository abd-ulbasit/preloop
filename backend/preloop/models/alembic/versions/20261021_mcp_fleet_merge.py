"""Merge MCP inventory with the published fleet and runner model migrations."""

revision = "20261021_mcp_fleet_merge"
down_revision = ("20261021_mcp_inventory", "20261021_fleet_models_merge")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join independent schema changes without additional DDL."""


def downgrade() -> None:
    """Leave schema rollback to each parent revision."""
