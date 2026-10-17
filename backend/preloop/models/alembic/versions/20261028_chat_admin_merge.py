"""Converge Cloud support and platform analytics without rewriting either history."""

revision = "20261028_chat_admin_merge"
down_revision = ("20261027_chat_support", "20261027_admin_analytics")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve both published migration histories."""
    pass


def downgrade() -> None:
    """Leave schema rollbacks to the respective parents."""
    pass
