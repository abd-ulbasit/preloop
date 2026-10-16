"""Add optional runner identity and tenant-local fleet lifecycle storage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261021_device_fleet"
down_revision = "20261020_jira_callback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep inert shared storage available to optional fleet services."""
    op.add_column("flow_runner", sa.Column("workstation_fingerprint", sa.String(64)))
    for name, columns, constraints in (
        (
            "fleet_finding",
            [
                sa.Column("rule", sa.String(64), nullable=False),
                sa.Column("subject_key", sa.String(255), nullable=False),
                sa.Column("device_key", sa.String(255)),
                sa.Column(
                    "user_id",
                    UUID(as_uuid=True),
                    sa.ForeignKey("user.id", ondelete="SET NULL"),
                ),
                sa.Column("state", sa.String(16), nullable=False),
                sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("resolved_at", sa.DateTime(timezone=True)),
                sa.Column(
                    "last_evaluated_at", sa.DateTime(timezone=True), nullable=False
                ),
                sa.Column("evidence", JSONB(), nullable=False),
                sa.Column("limitation", sa.String(1000), nullable=False),
            ],
            [
                sa.UniqueConstraint(
                    "account_id", "rule", "subject_key", name="uq_fleet_finding_subject"
                ),
                sa.CheckConstraint(
                    "state IN ('open', 'resolved', 'unknown', 'suppressed')",
                    name="ck_fleet_finding_state",
                ),
            ],
        ),
        (
            "fleet_alert_settings",
            [
                sa.Column("settings", JSONB(), nullable=False),
            ],
            [sa.UniqueConstraint("account_id", name="uq_fleet_alert_settings_account")],
        ),
    ):
        op.create_table(
            name,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "account_id",
                UUID(as_uuid=True),
                sa.ForeignKey("account.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            *columns,
            *constraints,
        )
        op.create_index(f"ix_{name}_id", name, ["id"])
        op.create_index(f"ix_{name}_account_id", name, ["account_id"])


def downgrade() -> None:
    """Drop fleet storage and optional runner identity."""
    op.drop_table("fleet_alert_settings")
    op.drop_table("fleet_finding")
    op.drop_column("flow_runner", "workstation_fingerprint")
