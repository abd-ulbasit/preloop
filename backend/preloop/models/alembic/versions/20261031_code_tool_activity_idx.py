"""Index the pull and merge request tool calls the Fleet Map counts.

Revision ID: 20261031_code_tool_idx
Revises: 20261030_gateway_model_idx

``GET /fleet/code-activity`` reads an account's successful code tool calls
(open, comment on, review, merge a pull or merge request) in a window.
``runtime_session_activity`` has only single-column indexes, so that read
would intersect the account and timestamp indexes over every tool call the
account ever made. This partial index holds just the code tool calls, keyed
by account and newest first, so the read is a short range scan.

The tool list must match ``CODE_ACTIVITY_TOOLS`` in
``preloop.models.crud.fleet_map``. Created CONCURRENTLY and IF NOT EXISTS,
with an invalid leftover dropped first, as in ``20261030_gateway_model_idx``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261031_code_tool_idx"
down_revision: Union[str, None] = "20261030_gateway_model_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ix_runtime_session_activity_code_tools"
TOOLS = (
    "add_comment",
    "add_comment_to_pending_review",
    "add_issue_comment",
    "approve_merge_request",
    "create_merge_request",
    "create_merge_request_note",
    "create_note",
    "create_pull_request",
    "create_pull_request_review",
    "merge_merge_request",
    "merge_pull_request",
    "pull_request_review_write",
    "submit_pending_pull_request_review",
    "update_pull_request",
)
PREDICATE = (
    "activity_type = 'tool_call' AND status = 'succeeded' AND tool_name IN ("
    + ", ".join(f"'{name}'" for name in TOOLS)
    + ")"
)
DEFINITION = (
    "ON runtime_session_activity (account_id, timestamp DESC) "
    f"INCLUDE (tool_name, flow_execution_id, runtime_session_id) WHERE {PREDICATE}"
)


def upgrade() -> None:
    """Build without blocking tool-call writes; repair an invalid prior attempt."""
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
            sa.text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} {DEFINITION}")
        )


def downgrade() -> None:
    """Remove only the code tool-call index."""
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}"))
