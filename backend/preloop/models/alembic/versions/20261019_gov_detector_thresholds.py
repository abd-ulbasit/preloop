"""Add review settings for the measured governance checks.

Revision ID: 20261019_gov_thresholds
Revises: 20261018_hosted_credit

Nullable JSON column. Existing rows keep null, which means the defaults and
any thresholds still written in the policy document apply.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261019_gov_thresholds"
down_revision = "20261018_hosted_credit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``governance_review_settings.detector_thresholds``."""
    op.add_column(
        "governance_review_settings",
        sa.Column(
            "detector_thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Review settings the measured checks read, keyed by rule kind. "
                "Null or a missing key keeps the default."
            ),
        ),
    )


def downgrade() -> None:
    """Drop ``governance_review_settings.detector_thresholds``."""
    op.drop_column("governance_review_settings", "detector_thresholds")
