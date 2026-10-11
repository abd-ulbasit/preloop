"""Per-flow result schema and its validation outcome.

Revision ID: 20261011_flow_result_schema
Revises: 20261011_runner_remote_sessions
Create Date: 2026-10-11

Issue #868. A flow may set an optional JSON Schema for its result.json
(``flow.result_schema``) and switch the check off (``flow.validate_result``).
The capture-time outcome is stored on the execution
(``flow_execution.result_schema_validation``), and the issue cost fact copies
the execution's failure category so the rollup export can name schema
mismatches. All new columns are nullable or defaulted: existing flows have no
schema and behave as before.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20261011_flow_result_schema"
down_revision: Union[str, None] = "20261011_runner_remote_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Alembic reads these module globals by name; keep a local reference so static
# analysis treats them as used.
_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"


def upgrade() -> None:
    op.add_column(
        "flow",
        sa.Column("result_schema", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "flow",
        sa.Column(
            "validate_result",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "flow_execution",
        sa.Column("result_schema_validation", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "issue_cost_execution",
        sa.Column("failure_category", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("issue_cost_execution", "failure_category")
    op.drop_column("flow_execution", "result_schema_validation")
    op.drop_column("flow", "validate_result")
    op.drop_column("flow", "result_schema")
