"""Store block and approval matches on policy_notice_hit.

Revision ID: 20261014_policy_notice_match
Revises: 20261014_tool_config_scope_nulls

Notify matches were the only rows in ``policy_notice_hit``. Deny and
require_approval matches were audit rows only, so a summary could not
count them or group them by developer. This adds the action, the model,
the runtime principal, the runtime session and the audit event id.

``action`` defaults to ``notify``. Adding the column NOT NULL with that
server default backfills every existing row in place.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261014_policy_notice_match"
down_revision: Union[str, None] = "20261014_tool_config_scope_nulls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

_ACTION_CHECK = "action IN ('notify', 'deny', 'require_approval', 'redact')"


def upgrade() -> None:
    """Add match columns and the account/action/time index."""
    op.add_column(
        "policy_notice_hit",
        sa.Column(
            "action",
            sa.String(length=32),
            nullable=False,
            server_default="notify",
            comment="notify | deny | require_approval | redact",
        ),
    )
    op.add_column(
        "policy_notice_hit",
        sa.Column("model_alias", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "policy_notice_hit",
        sa.Column(
            "agent_ref",
            sa.String(length=255),
            nullable=True,
            comment="Runtime principal, type:id when both are known",
        ),
    )
    op.add_column(
        "policy_notice_hit",
        sa.Column("runtime_session_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "policy_notice_hit",
        sa.Column(
            "audit_event_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="Audit row written for the same match",
        ),
    )
    op.create_check_constraint(
        "ck_policy_notice_hit_action",
        "policy_notice_hit",
        _ACTION_CHECK,
    )
    op.create_index(
        "ix_policy_notice_hit_account_action_created",
        "policy_notice_hit",
        ["account_id", "action", "created_at"],
    )


def downgrade() -> None:
    """Remove the match columns."""
    op.drop_index(
        "ix_policy_notice_hit_account_action_created",
        table_name="policy_notice_hit",
    )
    op.drop_constraint(
        "ck_policy_notice_hit_action",
        "policy_notice_hit",
        type_="check",
    )
    op.drop_column("policy_notice_hit", "audit_event_id")
    op.drop_column("policy_notice_hit", "runtime_session_id")
    op.drop_column("policy_notice_hit", "agent_ref")
    op.drop_column("policy_notice_hit", "model_alias")
    op.drop_column("policy_notice_hit", "action")
