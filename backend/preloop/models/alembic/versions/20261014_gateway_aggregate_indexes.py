"""Indexes for the gateway totals, subscription block and rate-limit snapshots.

Revision ID: 20261014_gateway_agg_idx
Revises: 20261011_session_alias

- ``ix_api_usage_gateway_totals`` (account_id, timestamp) INCLUDE the columns
  the gateway window aggregates read (#1583). The Spend and Gateway traffic
  totals and their per-model, per-flow and per-day breakdowns become
  index-only scans and never read the wide ``meta_data`` heap rows. It is
  partial on the same gateway + non-replay filter as
  ``ix_api_usage_gateway_spend``. That narrower index stays: the admin
  trailing-spend sum keeps its smaller index-only path, and dropping an index
  on api_usage is a separate, lock-sensitive change.
- ``ix_api_usage_gateway_subscription`` (account_id, timestamp), partial on
  subscription-billed gateway rows. The Spend subscription block filters on
  ``cost_source = 'subscription'``, which no index served: every Spend load
  ran a sequential scan of the whole api_usage table (#1583).
- ``ix_api_usage_rate_limit_snapshot`` (account_id, provider, model,
  timestamp DESC), partial on gateway rows that carry a rate-limit header
  snapshot (#1582). The latest snapshot per provider/model was a DISTINCT ON
  over every gateway row of the account's history; with this index it is a
  skip scan, one probe per pair.

Created CONCURRENTLY and IF NOT EXISTS, with a leftover invalid index
dropped first, as in ``20261013_admin_activity_indexes``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_gateway_agg_idx"
down_revision: Union[str, None] = "20261011_session_alias"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

_GATEWAY_NON_REPLAY = (
    "action_type = 'model_gateway' AND "
    "((meta_data ->> 'purpose') IS NULL OR "
    "(meta_data ->> 'purpose') <> 'replay_validation')"
)

INDEXES = {
    "ix_api_usage_gateway_totals": (
        "ON api_usage (account_id, timestamp) INCLUDE (id, status_code, "
        "prompt_tokens, completion_tokens, total_tokens, estimated_cost, "
        "cache_read_tokens, cache_creation_tokens, model_alias, provider_name, "
        f"ai_model_id, flow_id) WHERE {_GATEWAY_NON_REPLAY}"
    ),
    "ix_api_usage_gateway_subscription": (
        "ON api_usage (account_id, timestamp) "
        "WHERE action_type = 'model_gateway' AND cost_source = 'subscription'"
    ),
    "ix_api_usage_rate_limit_snapshot": (
        "ON api_usage (account_id, (COALESCE(provider_name, '')), "
        "(COALESCE(model_alias, '')), timestamp DESC) "
        "WHERE action_type = 'model_gateway' AND "
        "(meta_data ->> 'rate_limit') IS NOT NULL AND "
        "((meta_data ->> 'purpose') IS NULL OR "
        "(meta_data ->> 'purpose') <> 'replay_validation')"
    ),
}


def upgrade() -> None:
    """Create the indexes without blocking writes; rebuild invalid leftovers."""
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
    """Drop the indexes without locks."""
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
