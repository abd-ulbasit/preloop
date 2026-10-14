"""Add governance review tables (#1709).

Revision ID: 20261016_governance_review
Revises: 20261014_tool_config_scope_nulls
Create Date: 2026-10-14

Account-scoped store for the weekly governance review: the prose policy
document, per-account settings, one digest segment per session span, the
weekly run, and findings. Processing stays in an Enterprise plugin.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "20261016_governance_review"
down_revision = "20261014_tool_config_scope_nulls"
branch_labels = None
depends_on = None

_ALEMBIC_IDENTIFIERS = (revision, down_revision, branch_labels, depends_on)
assert _ALEMBIC_IDENTIFIERS, "Alembic revision metadata must be defined"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    """Create the governance review tables."""
    op.create_table(
        "governance_policy_document",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        *_timestamps(),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            comment="Per-account version, starting at 1",
        ),
        sa.Column(
            "body_markdown",
            sa.Text(),
            nullable=False,
            comment="Policy prose, at most 64 KiB",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Exactly one active version per account",
        ),
        sa.Column(
            "created_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            comment="default_template | user | restore",
        ),
        sa.UniqueConstraint(
            "account_id",
            "version",
            name="uq_governance_policy_document_account_version",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_governance_policy_document_version",
        ),
        sa.CheckConstraint(
            "octet_length(body_markdown) <= 65536",
            name="ck_governance_policy_document_body_len",
        ),
        sa.CheckConstraint(
            "source IN ('default_template', 'user', 'restore')",
            name="ck_governance_policy_document_source",
        ),
    )
    op.create_index(
        "uq_governance_policy_document_one_active",
        "governance_policy_document",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "governance_review_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        *_timestamps(),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "policy_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("governance_policy_document.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'running'"),
            comment="running | completed | partial | skipped | failed",
        ),
        sa.Column("skip_reason", sa.String(255), nullable=True),
        sa.Column(
            "estimated_cost_preflight",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "platform_cost",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "account_cost",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "sessions_considered",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "sessions_summarized",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "findings_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("mode_before", sa.String(32), nullable=True),
        sa.Column("mode_after", sa.String(32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "account_id",
            "window_start",
            name="uq_governance_review_run_account_window",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'partial', 'skipped', 'failed')",
            name="ck_governance_review_run_status",
        ),
        sa.CheckConstraint(
            "mode_before IS NULL OR mode_before IN "
            "('on_platform', 'off_over_budget', 'on_own_model', 'off')",
            name="ck_governance_review_run_mode_before",
        ),
        sa.CheckConstraint(
            "mode_after IS NULL OR mode_after IN "
            "('on_platform', 'off_over_budget', 'on_own_model', 'off')",
            name="ck_governance_review_run_mode_after",
        ),
        sa.CheckConstraint(
            "window_end >= window_start",
            name="ck_governance_review_run_window",
        ),
    )

    op.create_table(
        "governance_review_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        *_timestamps(),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.String(32),
            nullable=False,
            comment="on_platform | off_over_budget | on_own_model | off",
        ),
        sa.Column(
            "analysis_model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ai_model.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mode_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "mode_changed_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "last_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("governance_review_run.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "account_id",
            name="uq_governance_review_settings_account",
        ),
        sa.CheckConstraint(
            "mode IN ('on_platform', 'off_over_budget', 'on_own_model', 'off')",
            name="ck_governance_review_settings_mode",
        ),
    )

    op.create_table(
        "governance_session_digest",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        *_timestamps(),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "runtime_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("runtime_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "segment_index",
            sa.Integer(),
            nullable=False,
            comment="Zero-based segment of this session",
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "window_end",
            sa.DateTime(timezone=True),
            nullable=False,
            comment=("Stage B includes the segment when this falls in the review week"),
        ),
        sa.Column(
            "policy_document_version",
            sa.Integer(),
            nullable=True,
            comment=(
                "Active policy document version this segment was summarised under"
            ),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "digest",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Spec 4.3 structured digest, at most 4 KiB serialized",
        ),
        sa.Column(
            "summary",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Policy-aware generic digest for this segment",
        ),
        sa.Column(
            "observations",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="rule_ref, excerpt (<=280 chars), confidence",
        ),
        sa.Column(
            "rolling_summary",
            sa.Text(),
            nullable=True,
            comment="Context the next segment and Stage B read",
        ),
        sa.Column(
            "digest_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "request_watermark",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Request count covered by this segment",
        ),
        sa.Column("model_alias", sa.String(255), nullable=True),
        sa.Column(
            "input_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "estimated_cost",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "funding",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'platform'"),
            comment="platform | account",
        ),
        sa.Column(
            "truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.UniqueConstraint(
            "runtime_session_id",
            "segment_index",
            name="uq_governance_session_digest_segment",
        ),
        sa.CheckConstraint(
            "segment_index >= 0",
            name="ck_governance_session_digest_segment_index",
        ),
        sa.CheckConstraint(
            "window_end >= window_start",
            name="ck_governance_session_digest_window",
        ),
        sa.CheckConstraint(
            "funding IN ('platform', 'account')",
            name="ck_governance_session_digest_funding",
        ),
        sa.CheckConstraint(
            "policy_document_version IS NULL OR policy_document_version >= 1",
            name="ck_governance_session_digest_policy_version",
        ),
    )
    op.create_index(
        "ix_governance_session_digest_account_window_end",
        "governance_session_digest",
        ["account_id", "window_end"],
    )

    op.create_table(
        "governance_finding",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        *_timestamps(),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("governance_review_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
            comment="violation | inefficiency | anomaly",
        ),
        sa.Column(
            "category",
            sa.String(64),
            nullable=False,
            comment=(
                "unapproved_tool, unused_tools, cache_miss, "
                "and the rest of the taxonomy"
            ),
        ),
        sa.Column(
            "severity",
            sa.String(8),
            nullable=False,
            comment="high | medium | low",
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column(
            "evidence",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment=(
                "excerpt (<=280), source, optional audit_event_id and api_usage_id"
            ),
        ),
        sa.Column(
            "session_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
            comment="At most 20 runtime session ids",
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("agent_ref", sa.String(255), nullable=True),
        sa.Column(
            "suggestion",
            sa.Text(),
            nullable=False,
            comment="At most 600 characters",
        ),
        sa.Column("estimated_weekly_savings", sa.Float(), nullable=True),
        sa.Column(
            "fingerprint",
            sa.Text(),
            nullable=False,
            comment=("Unique per account. Include the review window to recur weekly."),
        ),
        sa.Column("rule_ref", sa.String(255), nullable=True),
        sa.Column(
            "dismissed_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "account_id",
            "fingerprint",
            name="uq_governance_finding_account_fingerprint",
        ),
        sa.CheckConstraint(
            "kind IN ('violation', 'inefficiency', 'anomaly')",
            name="ck_governance_finding_kind",
        ),
        sa.CheckConstraint(
            "severity IN ('high', 'medium', 'low')",
            name="ck_governance_finding_severity",
        ),
        sa.CheckConstraint(
            "char_length(title) <= 160",
            name="ck_governance_finding_title_len",
        ),
        sa.CheckConstraint(
            "char_length(suggestion) <= 600",
            name="ck_governance_finding_suggestion_len",
        ),
        sa.CheckConstraint(
            "cardinality(session_ids) <= 20",
            name="ck_governance_finding_session_ids",
        ),
    )


def downgrade() -> None:
    """Drop the governance review tables."""
    op.drop_table("governance_finding")
    op.drop_index(
        "ix_governance_session_digest_account_window_end",
        table_name="governance_session_digest",
    )
    op.drop_table("governance_session_digest")
    op.drop_table("governance_review_settings")
    op.drop_table("governance_review_run")
    op.drop_index(
        "uq_governance_policy_document_one_active",
        table_name="governance_policy_document",
    )
    op.drop_table("governance_policy_document")
