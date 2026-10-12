"""Add runner scheduling preferences.

Revision ID: 20261011_runner_scheduling
Revises: 20261012_identity_backfill
"""

import sqlalchemy as sa
from alembic import op

revision = "20261011_runner_scheduling"
down_revision = "20261012_identity_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Default to accepting only tagged work, with the runner unpaused."""
    # Existing rows also opt out of untagged jobs, as required by the new
    # scheduling default. Operators must enable the console toggle to keep
    # routing unpinned flows to these runners after upgrading.
    for name in ("paused", "accept_untagged"):
        op.add_column(
            "flow_runner",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    """Remove scheduling preferences."""
    for name in ("accept_untagged", "paused"):
        op.drop_column("flow_runner", name)
