"""Index stable execution pagination without blocking writes.

Revision ID: 20261014_execution_end_idx
Revises: 20261014_gateway_agg_idx

The status prefix serves failure filtering; completion time and ID match the
page order and the recent-failure count window. A start_time/id index also
serves stable default pagination by backward index scan. Invalid interrupted
builds are rebuilt on retry, following the existing concurrent-index migrations.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_execution_end_idx"
down_revision: Union[str, None] = "20261014_gateway_agg_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

INDEXES = {
    "ix_flow_execution_status_completion": (
        "ON flow_execution (status, end_time DESC NULLS LAST, id DESC)"
    ),
    "ix_flow_execution_start_time_id": "ON flow_execution (start_time, id)",
}


def upgrade() -> None:
    """Build the indexes concurrently, repairing interrupted prior builds."""
    with op.get_context().autocommit_block():
        for name, definition in INDEXES.items():
            invalid = (
                op.get_bind()
                .execute(
                    sa.text(
                        "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                        "WHERE c.relname = :name AND NOT i.indisvalid"
                    ),
                    {"name": name},
                )
                .scalar()
            )
            if invalid:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} {definition}")


def downgrade() -> None:
    """Drop the pagination indexes concurrently."""
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
