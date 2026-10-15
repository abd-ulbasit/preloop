"""Add prepaid credit lots, ledger, and reservation split columns.

Revision ID: 20261018_hosted_credit
Revises: 20261017_gov_candidate_idx

Empty tables. No existing allowance row is rewritten. ``credit_reserved``
defaults to 0 so current readers of ``hosted_spend_reservation.reserved``
are unchanged.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261018_hosted_credit"
down_revision = "20261017_gov_candidate_idx"
branch_labels = None
depends_on = None

_KIND_SQL = (
    "kind IN ('purchase', 'consumption', 'refund', 'chargeback', "
    "'adjustment', 'promotional_grant', 'expiry')"
)
_SOURCE_SQL = "source IN ('checkout', 'auto_topup')"
_STATUS_SQL = (
    "status IN ('pending', 'granted', 'failed', 'refunded', 'charged_back', 'expired')"
)


def _money(name: str, *, nullable: bool = False) -> sa.Column:
    if nullable:
        return sa.Column(name, sa.Numeric(20, 10), nullable=True)
    return sa.Column(name, sa.Numeric(20, 10), nullable=False, server_default="0")


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    ]


def _account_id() -> sa.Column:
    return sa.Column(
        "account_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "hosted_spend_reservation",
        sa.Column(
            "credit_reserved",
            sa.Numeric(20, 10),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "hosted_spend_reservation",
        sa.Column("price_multiplier", sa.Numeric(6, 4), nullable=True),
    )
    op.create_table(
        "hosted_credit_account",
        *_common(),
        _account_id(),
        _money("purchased"),
        _money("consumed"),
        _money("reserved"),
        _money("refunded"),
        _money("expired"),
        _money("balance"),
        sa.UniqueConstraint("account_id", name="uq_hosted_credit_account"),
        sa.CheckConstraint(
            "balance = purchased - consumed - refunded - expired",
            name="ck_hosted_credit_account_balance",
        ),
        sa.CheckConstraint(
            "reserved >= 0",
            name="ck_hosted_credit_account_reserved",
        ),
        sa.CheckConstraint(
            "purchased >= 0 AND consumed >= 0 AND refunded >= 0 AND expired >= 0",
            name="ck_hosted_credit_account_components",
        ),
    )
    op.create_table(
        "hosted_credit_purchase",
        *_common(),
        _account_id(),
        sa.Column("pack_id", sa.String(64), nullable=False),
        sa.Column("amount_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stripe_checkout_session_id", sa.String(255), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(255), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        _money("remaining_usd"),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_SOURCE_SQL, name="ck_hosted_credit_purchase_source"),
        sa.CheckConstraint(_STATUS_SQL, name="ck_hosted_credit_purchase_status"),
        sa.CheckConstraint(
            "remaining_usd >= 0",
            name="ck_hosted_credit_purchase_remaining",
        ),
        sa.UniqueConstraint(
            "stripe_checkout_session_id",
            name="uq_hosted_credit_checkout_session",
        ),
        sa.UniqueConstraint(
            "stripe_payment_intent_id",
            name="uq_hosted_credit_payment_intent",
        ),
    )
    op.create_index(
        "ix_hosted_credit_purchase_account_id",
        "hosted_credit_purchase",
        ["account_id"],
    )
    op.create_index(
        "ix_hosted_credit_purchase_fifo",
        "hosted_credit_purchase",
        ["account_id", "granted_at", "id"],
    )
    op.create_table(
        "hosted_credit_ledger",
        *_common(),
        _account_id(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(20, 10), nullable=False),
        sa.Column("balance_after", sa.Numeric(20, 10), nullable=False),
        sa.Column("operation_key", sa.String(128), nullable=False),
        sa.Column(
            "purchase_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hosted_credit_purchase.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_cost", sa.Numeric(20, 10), nullable=True),
        sa.Column("price_multiplier", sa.Numeric(6, 4), nullable=True),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "account_id", "operation_key", name="uq_hosted_credit_operation"
        ),
        sa.CheckConstraint("amount > 0", name="ck_hosted_credit_ledger_amount"),
        sa.CheckConstraint(_KIND_SQL, name="ck_hosted_credit_ledger_kind"),
    )
    op.create_index(
        "ix_hosted_credit_ledger_account_id",
        "hosted_credit_ledger",
        ["account_id"],
    )
    op.create_table(
        "hosted_credit_topup_setting",
        *_common(),
        _account_id(),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "threshold_usd",
            sa.Numeric(20, 10),
            nullable=False,
            server_default="2.00",
        ),
        sa.Column(
            "pack_id",
            sa.String(64),
            nullable=False,
            server_default="hosted_credits_10",
        ),
        sa.Column("monthly_ceiling_usd", sa.Numeric(20, 10), nullable=True),
        sa.Column("period", sa.Date(), nullable=True),
        _money("spent_this_period"),
        sa.Column("disabled_reason", sa.String(200), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(200), nullable=True),
        sa.UniqueConstraint("account_id", name="uq_hosted_credit_topup_account"),
    )
    for table in (
        "hosted_credit_account",
        "hosted_credit_purchase",
        "hosted_credit_ledger",
        "hosted_credit_topup_setting",
    ):
        op.create_index(f"ix_{table}_id", table, ["id"])


def downgrade() -> None:
    for table in (
        "hosted_credit_ledger",
        "hosted_credit_topup_setting",
        "hosted_credit_purchase",
        "hosted_credit_account",
    ):
        op.drop_table(table)
    op.drop_column("hosted_spend_reservation", "price_multiplier")
    op.drop_column("hosted_spend_reservation", "credit_reserved")
