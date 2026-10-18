"""Keep api_usage's visibility map current so gateway aggregates stay index-only.

Revision ID: 20261030_api_usage_autovac
Revises: 20261029_audit_unsealed_idx

``GET /ai-models/overview`` took 15-30 s in production. Its per-model
aggregate (``get_gateway_usage_by_model``) has a covering partial index,
``ix_api_usage_gateway_totals`` (20261014), but an index-only scan only skips
the heap for pages the visibility map marks all-visible. With the default
autovacuum thresholds (20 % of a 5.4M-row table) the recent, hot part of
``api_usage`` is rarely vacuumed, so the planner costs the covering index as
a heap scan and reads ~166k heap pages from disk instead.

- Lower the per-table autovacuum and auto-analyze thresholds on ``api_usage``
  so inserts and updates are vacuumed in small batches. ``ALTER TABLE ... SET``
  takes SHARE UPDATE EXCLUSIVE, which does not block reads or writes.
- Rebuild ``ix_api_usage_gateway_totals`` if it is missing or was left
  invalid by an interrupted concurrent build, as in
  ``20261018_gov_spend_idx``.

Operators should run ``VACUUM (ANALYZE) api_usage`` once after deploying; it
sets the visibility map immediately instead of waiting for autovacuum.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261030_api_usage_autovac"
down_revision: Union[str, None] = "20261029_audit_unsealed_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ix_api_usage_gateway_totals"
INDEX_DEFINITION = (
    "ON api_usage (account_id, timestamp) INCLUDE (id, status_code, "
    "prompt_tokens, completion_tokens, total_tokens, estimated_cost, "
    "cache_read_tokens, cache_creation_tokens, model_alias, provider_name, "
    "ai_model_id, flow_id) WHERE action_type = 'model_gateway' AND "
    "((meta_data ->> 'purpose') IS NULL OR "
    "(meta_data ->> 'purpose') <> 'replay_validation')"
)
AUTOVACUUM = {
    "autovacuum_vacuum_scale_factor": "0.01",
    "autovacuum_vacuum_insert_scale_factor": "0.01",
    "autovacuum_analyze_scale_factor": "0.01",
}


def upgrade() -> None:
    """Tune autovacuum on api_usage and make sure the covering index is valid."""
    settings = ", ".join(f"{key} = {value}" for key, value in AUTOVACUUM.items())
    op.execute(sa.text(f"ALTER TABLE api_usage SET ({settings})"))
    with op.get_context().autocommit_block():
        valid = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT i.indisvalid FROM pg_index i JOIN pg_class c "
                    "ON c.oid = i.indexrelid WHERE c.relname = :name"
                ),
                {"name": INDEX},
            )
            .scalar()
        )
        if valid is False:
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}"))
        op.execute(
            sa.text(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} {INDEX_DEFINITION}"
            )
        )


def downgrade() -> None:
    """Restore the server-wide autovacuum defaults; the index belongs to 20261014."""
    op.execute(sa.text(f"ALTER TABLE api_usage RESET ({', '.join(AUTOVACUUM)})"))
