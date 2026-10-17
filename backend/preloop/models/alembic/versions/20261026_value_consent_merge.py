"""Converge the owner-consent schema and weekly outcome cache."""

revision = "20261026_value_consent_merge"
down_revision = ("20261025_weekly_value_merge", "20261022_owner_consent")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve both published migration histories."""
    pass


def downgrade() -> None:
    """Leave schema rollbacks to the respective parents."""
    pass
