"""Store runner presence signals.

Revision ID: 20261011_runner_presence
Revises: 20261011_harness_inventory
Create Date: 2026-10-11

Issue #1506. The server derives runner presence (online, busy, idle, asleep,
offline) from heartbeats. These nullable columns hold the sleep signal, the
reported OS idle time and the last derived state with its start time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261011_runner_presence"
down_revision: Union[str, None] = "20261011_harness_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Alembic reads these module globals by name; keep a local reference so static
# analysis treats them as used.
_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"


def upgrade() -> None:
    """Add the nullable presence columns to flow_runner."""
    op.add_column(
        "flow_runner",
        sa.Column("sleep_signaled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "flow_runner", sa.Column("os_idle_seconds", sa.Integer(), nullable=True)
    )
    op.add_column(
        "flow_runner", sa.Column("presence_state", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "flow_runner",
        sa.Column("presence_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the presence columns."""
    for column in (
        "presence_since",
        "presence_state",
        "os_idle_seconds",
        "sleep_signaled_at",
    ):
        op.drop_column("flow_runner", column)
