"""Index each account's unsealed audit tail for the chain status count."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_audit_unsealed_idx"
down_revision: Union[str, None] = "20261018_gov_spend_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

NAME = "ix_audit_log_account_unsealed"


def upgrade() -> None:
    """Build the partial index concurrently, repairing an interrupted build."""
    with op.get_context().autocommit_block():
        invalid = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT 1 FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relname = :name "
                    "AND n.nspname = current_schema() AND NOT i.indisvalid"
                ),
                {"name": NAME},
            )
            .scalar()
        )
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {NAME}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {NAME} "
            "ON audit_log (account_id) WHERE chain_seq IS NULL"
        )


def downgrade() -> None:
    """Remove the index without blocking audit writes."""
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {NAME}")
