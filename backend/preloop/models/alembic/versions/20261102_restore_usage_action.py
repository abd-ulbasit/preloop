"""Restore action types retained before a manual governance usage relabel.

Revision ID: 20261102_restore_usage_action
Revises: 20261101_customer_usage_idx
"""

from alembic import op

revision = "20261102_restore_usage_action"
down_revision = "20261101_customer_usage_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Restore only governance rows with retained provenance and mark changes."""
    op.execute("""
        UPDATE api_usage
        SET meta_data = jsonb_set(meta_data, '{report_restore_action_type}',
                                  coalesce(to_jsonb(action_type), 'null'::jsonb)),
            action_type = meta_data ->> 'action_type_before'
        WHERE left(meta_data ->> 'purpose', 11) = 'governance_'
          AND nullif(meta_data ->> 'action_type_before', '') IS NOT NULL
          AND action_type IS DISTINCT FROM meta_data ->> 'action_type_before'
          AND NOT (meta_data ? 'report_restore_action_type')
    """)


def downgrade() -> None:
    """Undo only this migration's changes when the restored value is intact."""
    op.execute("""
        UPDATE api_usage
        SET action_type = meta_data ->> 'report_restore_action_type',
            meta_data = meta_data - 'report_restore_action_type'
        WHERE meta_data ? 'report_restore_action_type'
          AND action_type = meta_data ->> 'action_type_before'
    """)
