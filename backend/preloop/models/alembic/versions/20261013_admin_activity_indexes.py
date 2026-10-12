"""Index event and gateway usage for the admin accounts list.

Revision ID: 20261013_admin_activity_idx
Revises: 20261013_note_receipts

The admin accounts list sorts by last activity and shows 30-day gateway
spend per account. Both aggregated whole tables on every page load:
``MAX(event.timestamp) GROUP BY account_id`` read every event row, and the
spend sum read every gateway row (including the wide ``meta_data`` JSON) for
the listed accounts. On prod the request ran past the ingress timeout.

- ``ix_event_account_timestamp`` (account_id, timestamp DESC): the latest
  event of an account is one backward index probe.
- ``ix_api_usage_gateway_spend`` (account_id, timestamp) INCLUDE
  (estimated_cost), partial on the gateway + non-replay filter that
  ``exclude_replay_usage_condition`` applies: the trailing spend is an
  index-only scan.

Created CONCURRENTLY (no write lock on the two busiest tables) and
IF NOT EXISTS, with a leftover invalid index dropped first, as in
``20261004_artifact_created_idx``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_admin_activity_idx"
down_revision: Union[str, None] = "20261013_note_receipts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

INDEXES = {
    "ix_event_account_timestamp": ("ON event (account_id, timestamp DESC)"),
    "ix_api_usage_gateway_spend": (
        "ON api_usage (account_id, timestamp) INCLUDE (estimated_cost) "
        "WHERE action_type = 'model_gateway' AND "
        "((meta_data ->> 'purpose') IS NULL OR "
        "(meta_data ->> 'purpose') <> 'replay_validation')"
    ),
}


def upgrade() -> None:
    """Create both indexes without blocking writes; rebuild invalid leftovers."""
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        for name, definition in INDEXES.items():
            invalid = bind.execute(
                sa.text(
                    "SELECT 1 FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = :name AND NOT i.indisvalid"
                ),
                {"name": name},
            ).scalar()
            if invalid:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} {definition}")


def downgrade() -> None:
    """Drop both indexes without blocking writes."""
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
