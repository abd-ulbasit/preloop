"""Make the account-wide tool configuration scope unique and remove duplicates.

Revision ID: 20261014_tool_config_scope_nulls
Revises: 20261014_execution_end_idx

``uq_account_tool_source`` spans two nullable scope columns. Postgres treats
NULLs as distinct in a plain unique constraint, so two concurrent
get-or-create calls for a native tool inserted identical account-wide rows
(seen in production: two ``WebFetch`` rows 1.3 ms apart). Every lookup that
expected one row then failed with ``MultipleResultsFound`` and the agent
permission check answered 500.

This migration folds duplicates into the oldest row (re-pointing access rules
and approval requests so no history is lost) and recreates the constraint with
``NULLS NOT DISTINCT`` (PostgreSQL 15+). Older servers get an equivalent
unique expression index under the same name.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_tool_config_scope_nulls"
down_revision: Union[str, None] = "20261014_execution_end_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

CONSTRAINT = "uq_account_tool_source"
SCOPE_COLUMNS = "account_id, tool_name, tool_source, mcp_server_id, managed_agent_id"
NIL_UUID = "00000000-0000-0000-0000-000000000000"

# Oldest row per scope group survives; the rest are folded into it.
DEDUPE_SQL = f"""
WITH ranked AS (
    SELECT id,
           first_value(id) OVER (
               PARTITION BY account_id, tool_name, tool_source,
                            COALESCE(mcp_server_id, '{NIL_UUID}'::uuid),
                            COALESCE(managed_agent_id, '{NIL_UUID}'::uuid)
               ORDER BY created_at, id
           ) AS keeper_id
    FROM tool_configuration
),
losers AS (
    SELECT id, keeper_id FROM ranked WHERE id <> keeper_id
),
moved_rules AS (
    UPDATE tool_access_rules r
       SET tool_configuration_id = l.keeper_id
      FROM losers l
     WHERE r.tool_configuration_id = l.id
),
moved_requests AS (
    UPDATE approval_request a
       SET tool_configuration_id = l.keeper_id
      FROM losers l
     WHERE a.tool_configuration_id = l.id
)
DELETE FROM tool_configuration t USING losers l WHERE t.id = l.id
"""


def _drop_existing() -> None:
    op.execute(f"ALTER TABLE tool_configuration DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(f"DROP INDEX IF EXISTS {CONSTRAINT}")


def upgrade() -> None:
    """Fold duplicate scope rows into the oldest, then tighten the constraint."""
    bind = op.get_bind()
    bind.execute(sa.text(DEDUPE_SQL))
    _drop_existing()
    version = bind.execute(sa.text("SHOW server_version_num")).scalar()
    if int(version or 0) >= 150000:
        op.execute(
            f"ALTER TABLE tool_configuration ADD CONSTRAINT {CONSTRAINT} "
            f"UNIQUE NULLS NOT DISTINCT ({SCOPE_COLUMNS})"
        )
    else:
        op.execute(
            f"CREATE UNIQUE INDEX {CONSTRAINT} ON tool_configuration ("
            "account_id, tool_name, tool_source, "
            f"COALESCE(mcp_server_id, '{NIL_UUID}'::uuid), "
            f"COALESCE(managed_agent_id, '{NIL_UUID}'::uuid))"
        )


def downgrade() -> None:
    """Restore the NULLs-distinct constraint (folded rows are not restored)."""
    _drop_existing()
    op.create_unique_constraint(
        CONSTRAINT,
        "tool_configuration",
        ["account_id", "tool_name", "tool_source", "mcp_server_id", "managed_agent_id"],
    )
