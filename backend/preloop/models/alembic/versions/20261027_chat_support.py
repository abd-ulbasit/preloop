"""Add expiring chat setup, listener health and generic support persistence."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20261027_chat_support"
down_revision = "20261026_value_consent_merge"
branch_labels = None
depends_on = None


def base() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    ]


def fk(name: str, table: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        UUID(as_uuid=True),
        sa.ForeignKey(table + ".id", ondelete="CASCADE"),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "chat_setup_draft",
        *base(),
        fk("account_id", "account"),
        fk("user_id", "user"),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("state_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime()),
        sa.Column("oauth_digest", sa.String(64), unique=True),
    )
    op.create_index(
        "ix_chat_setup_draft_account_id", "chat_setup_draft", ["account_id"]
    )
    op.create_table(
        "chat_listener",
        *base(),
        fk("connection_id", "chat_connection"),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("last_event_at", sa.DateTime()),
        sa.Column("connected_at", sa.DateTime()),
        sa.Column("last_error", sa.String(160)),
        sa.UniqueConstraint("connection_id"),
    )
    op.create_table(
        "support_conversation",
        *base(),
        fk("account_id", "account", True),
        fk("user_id", "user", True),
        sa.Column("access_digest", sa.String(64), unique=True, nullable=False),
        sa.Column("source_page", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("ownership_generation", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.String(256)),
        sa.Column("next_sequence", sa.Integer(), nullable=False),
        sa.Column("operator_root_id", sa.String(256), unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("contact_encrypted", sa.Text()),
    )
    op.create_index(
        "ix_support_conversation_account_id", "support_conversation", ["account_id"]
    )
    op.create_index(
        "ix_support_conversation_expires_at", "support_conversation", ["expires_at"]
    )
    op.create_table(
        "support_message",
        *base(),
        fk("conversation_id", "support_conversation"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(256), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content_encrypted", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("ownership_generation", sa.Integer(), nullable=False),
        sa.Column("delivery_status", sa.String(20), nullable=False),
        sa.UniqueConstraint("conversation_id", "sequence"),
        sa.UniqueConstraint("conversation_id", "event_id"),
    )
    op.create_index(
        "ix_support_message_conversation_id", "support_message", ["conversation_id"]
    )
    op.create_table(
        "support_outbox",
        *base(),
        fk("conversation_id", "support_conversation"),
        fk("message_id", "support_message"),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ownership_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("provider_message_id", sa.String(256)),
        sa.Column("last_error", sa.String(160)),
        sa.UniqueConstraint("message_id", "kind"),
    )
    op.create_index(
        "ix_support_outbox_conversation_id", "support_outbox", ["conversation_id"]
    )
    op.create_index("ix_support_outbox_status", "support_outbox", ["status"])
    op.create_table(
        "support_quota",
        *base(),
        sa.Column("bucket", sa.String(160), unique=True, nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("reserved_microusd", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_support_quota_expires_at", "support_quota", ["expires_at"])
    op.create_table(
        "service_cursor",
        *base(),
        sa.Column("name", sa.String(120), unique=True, nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("state", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    for name in (
        "service_cursor",
        "support_quota",
        "support_outbox",
        "support_message",
        "support_conversation",
        "chat_listener",
        "chat_setup_draft",
    ):
        op.drop_table(name)
