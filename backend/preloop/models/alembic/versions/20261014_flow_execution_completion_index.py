"""Index completion monitoring without blocking execution writes.

Revision ID: 20261014_execution_end_idx
Revises: 20261014_gateway_agg_idx

The status prefix serves failure filtering; completion time and ID match the
page order and the recent-failure count window. Invalid interrupted builds
are rebuilt on retry, following the existing concurrent-index migrations.
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

INDEX = "ix_flow_execution_status_completion"


def upgrade() -> None:
    """Build the index concurrently, repairing an interrupted prior build."""
    with op.get_context().autocommit_block():
        invalid = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = :name AND NOT i.indisvalid"
                ),
                {"name": INDEX},
            )
            .scalar()
        )
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} "
            "ON flow_execution (status, end_time DESC NULLS LAST, id DESC)"
        )


def downgrade() -> None:
    """Drop the completion index concurrently."""
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
