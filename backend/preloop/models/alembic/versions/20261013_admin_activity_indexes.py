"""Index event and gateway usage for the admin accounts list.

Revision ID: 20261013_admin_activity_idx
Revises: 20261011_runner_scheduling

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

``ix_event_account_id (account_id)`` is dropped after the composite exists:
the composite leads with ``account_id`` and serves every lookup the
single-column index did (including the ``ON DELETE SET NULL`` foreign key),
so keeping both would only add write cost on the busiest table.

Created CONCURRENTLY (no write lock on the two busiest tables) and
IF NOT EXISTS, with a leftover invalid index dropped first, as in
``20261004_artifact_created_idx``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_admin_activity_idx"
down_revision: Union[str, None] = "20261011_runner_scheduling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

# Superseded by ix_event_account_timestamp; dropped after it is built.
REPLACED_INDEX = "ix_event_account_id"
REPLACED_INDEX_DEFINITION = "ON event (account_id)"

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
        # Only after the composite is built and valid, so account lookups
        # always have an index.
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {REPLACED_INDEX}")


def downgrade() -> None:
    """Restore the single-column index, then drop the new ones, without locks."""
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {REPLACED_INDEX} "
            f"{REPLACED_INDEX_DEFINITION}"
        )
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
