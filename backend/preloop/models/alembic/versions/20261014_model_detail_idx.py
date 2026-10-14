"""Index model-detail usage by account, model and request time.

Revision ID: 20261014_model_detail_idx
Revises: 20261016_governance_review

``GET /ai-models/{id}/interactions`` and ``GET /ai-models/{id}/runtime-sessions``
filtered a busy model's gateway rows through indexes that lead with
``account_id`` or ``ai_model_id`` alone. The interaction count then hash-joined
every search document in the window, and the session list counted every
session that had used the model. On a model with tens of thousands of
requests that held a heavy-pool slot for about 15 seconds (#1688).

``ix_api_usage_account_model_ts`` is

``ON api_usage (account_id, ai_model_id, timestamp DESC, id DESC)
INCLUDE (runtime_session_id)
WHERE action_type = 'model_gateway' AND ai_model_id IS NOT NULL``.

``timestamp`` is the request time. The ``id`` tie-break lets
``ORDER BY timestamp DESC, id DESC LIMIT`` stop in index order. The included
session id lets the session page group without reading the heap.

Created CONCURRENTLY and IF NOT EXISTS, with a leftover invalid index dropped
first, as in ``20261014_gateway_aggregate_indexes``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_model_detail_idx"
down_revision: Union[str, None] = "20261016_governance_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

INDEX = "ix_api_usage_account_model_ts"
DEFINITION = (
    "ON api_usage (account_id, ai_model_id, timestamp DESC, id DESC) "
    "INCLUDE (runtime_session_id) "
    "WHERE action_type = 'model_gateway' AND ai_model_id IS NOT NULL"
)


def upgrade() -> None:
    """Create the index without blocking writes; rebuild an invalid leftover."""
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        invalid = bind.execute(
            sa.text(
                "SELECT 1 FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": INDEX},
        ).scalar()
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} {DEFINITION}")


def downgrade() -> None:
    """Drop the index without locks."""
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
