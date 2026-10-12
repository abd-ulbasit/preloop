"""Desktop endpoints and brokered desktop views.

Revision ID: 20261011_desktop_views
Revises: 20261013_note_receipts
Create Date: 2026-10-11

Issue #870. ``desktop_endpoint`` records hosts running ``preloop desktop
serve``; ``desktop_view`` records each time-boxed operator view. No frames or
secrets are stored: the view token is kept only as a sha256 hash.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20261011_desktop_views"
down_revision: Union[str, None] = "20261013_note_receipts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Alembic reads these module globals by name; keep a local reference so static
# analysis treats them as used.
_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"

_UUID = postgresql.UUID(as_uuid=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "desktop_endpoint",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "account_id",
            _UUID,
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint_id", sa.String(128), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False, server_default=""),
        sa.Column("display", sa.String(32), nullable=False, server_default=":99"),
        sa.Column(
            "managed_agent_id",
            _UUID,
            sa.ForeignKey("managed_agent.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "runner_id",
            _UUID,
            sa.ForeignKey("flow_runner.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_api_key_id",
            _UUID,
            sa.ForeignKey("api_key.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state", sa.String(16), nullable=False, server_default="offline"),
        sa.Column("connection_id", sa.String(64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "account_id", "endpoint_id", name="uq_desktop_endpoint_account_endpoint"
        ),
    )
    op.create_index("ix_desktop_endpoint_id", "desktop_endpoint", ["id"])
    op.create_index(
        "ix_desktop_endpoint_account_agent",
        "desktop_endpoint",
        ["account_id", "managed_agent_id"],
    )
    op.create_index(
        "ix_desktop_endpoint_account_runner",
        "desktop_endpoint",
        ["account_id", "runner_id"],
    )
    op.create_table(
        "desktop_view",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "account_id",
            _UUID,
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "endpoint_pk",
            _UUID,
            sa.ForeignKey("desktop_endpoint.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            _UUID,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("managed_agent_id", _UUID, nullable=True),
        sa.Column("runtime_session_id", _UUID, nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="view_only"),
        sa.Column("view_token_hash", sa.String(64), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("live_at", sa.DateTime(), nullable=True),
        sa.Column("viewer_attached_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_reason", sa.String(64), nullable=True),
        sa.Column("control_expires_at", sa.DateTime(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_desktop_view_id", "desktop_view", ["id"])
    op.create_index(
        "ix_desktop_view_endpoint_state", "desktop_view", ["endpoint_pk", "state"]
    )
    op.create_index(
        "ix_desktop_view_account_user", "desktop_view", ["account_id", "user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_desktop_view_account_user", table_name="desktop_view")
    op.drop_index("ix_desktop_view_endpoint_state", table_name="desktop_view")
    op.drop_index("ix_desktop_view_id", table_name="desktop_view")
    op.drop_table("desktop_view")
    op.drop_index("ix_desktop_endpoint_account_runner", table_name="desktop_endpoint")
    op.drop_index("ix_desktop_endpoint_account_agent", table_name="desktop_endpoint")
    op.drop_index("ix_desktop_endpoint_id", table_name="desktop_endpoint")
    op.drop_table("desktop_endpoint")
