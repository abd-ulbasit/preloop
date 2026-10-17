"""Index bounded platform model spend and report engagement reads."""

from alembic import op
import sqlalchemy as sa

revision = "20261027_admin_analytics"
down_revision = "20261026_value_consent_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Build partial indexes without blocking ledger writers."""
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_api_usage_admin_platform",
            "api_usage",
            ["timestamp", "account_id"],
            postgresql_where=sa.text(
                "action_type = 'model_gateway' AND meta_data ->> 'funding' = 'platform'"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_api_usage_admin_legacy",
            "api_usage",
            ["ai_model_id", "timestamp"],
            postgresql_where=sa.text(
                "action_type = 'model_gateway' AND meta_data ->> 'funding' IS NULL"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_event_report_window",
            "event",
            ["timestamp", "account_id", "event_type"],
            postgresql_where=sa.text(
                "event_type IN ('report_sent', 'report_open', 'report_click', 'report_unsubscribe')"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Drop only the analytics indexes."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_api_usage_admin_legacy",
            table_name="api_usage",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_event_report_window",
            table_name="event",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_api_usage_admin_platform",
            table_name="api_usage",
            postgresql_concurrently=True,
            if_exists=True,
        )
