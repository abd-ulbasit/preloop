"""Prepaid credit lots, an append-only ledger, and auto top-up settings.

Empty tables change no existing allowance path. Callers that pass no credit
balance keep today's behaviour. Stripe, purchase, and the UI live elsewhere.
An absent ``hosted_credit_topup_setting`` row means auto top-up is off; no
code path in this package inserts one.
"""

from datetime import date, datetime
from decimal import Decimal
import uuid

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Boolean

from .base import Base

LEDGER_KINDS = (
    "purchase",
    "consumption",
    "refund",
    "chargeback",
    "adjustment",
    "promotional_grant",
    "expiry",
)
PURCHASE_SOURCES = ("checkout", "auto_topup")
PURCHASE_STATUSES = (
    "pending",
    "granted",
    "failed",
    "refunded",
    "charged_back",
    "expired",
)
GRANT_KINDS = ("purchase", "promotional_grant", "adjustment")

_KIND_SQL = "kind IN ({})".format(", ".join(repr(kind) for kind in LEDGER_KINDS))
_SOURCE_SQL = "source IN ({})".format(
    ", ".join(repr(source) for source in PURCHASE_SOURCES)
)
_STATUS_SQL = "status IN ({})".format(
    ", ".join(repr(status) for status in PURCHASE_STATUSES)
)


class HostedCreditAccount(Base):
    """One balance cache per account. The ledger is the history."""

    __tablename__ = "hosted_credit_account"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_hosted_credit_account"),
        CheckConstraint(
            "balance = purchased - consumed - refunded - expired",
            name="ck_hosted_credit_account_balance",
        ),
        CheckConstraint(
            "reserved >= 0",
            name="ck_hosted_credit_account_reserved",
        ),
        CheckConstraint(
            "purchased >= 0 AND consumed >= 0 AND refunded >= 0 AND expired >= 0",
            name="ck_hosted_credit_account_components",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
    )
    purchased: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal(0), server_default="0"
    )
    consumed: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal(0), server_default="0"
    )
    reserved: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal(0), server_default="0"
    )
    refunded: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal(0), server_default="0"
    )
    expired: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal(0), server_default="0"
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal(0), server_default="0"
    )


class HostedCreditLedger(Base):
    """Append-only credit movement. A mistake is a compensating entry."""

    __tablename__ = "hosted_credit_ledger"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "operation_key", name="uq_hosted_credit_operation"
        ),
        CheckConstraint("amount > 0", name="ck_hosted_credit_ledger_amount"),
        CheckConstraint(_KIND_SQL, name="ck_hosted_credit_ledger_kind"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hosted_credit_purchase.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Hosted-spend reservation this movement settles, when the caller has one.
    # No foreign key: the allowance table must stay droppable on its own.
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    provider_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    price_multiplier: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class HostedCreditPurchase(Base):
    """One purchased lot: its own clock and its own remaining balance."""

    __tablename__ = "hosted_credit_purchase"
    __table_args__ = (
        CheckConstraint(_SOURCE_SQL, name="ck_hosted_credit_purchase_source"),
        CheckConstraint(_STATUS_SQL, name="ck_hosted_credit_purchase_status"),
        CheckConstraint(
            "remaining_usd >= 0",
            name="ck_hosted_credit_purchase_remaining",
        ),
        Index(
            "ix_hosted_credit_purchase_fifo",
            "account_id",
            "granted_at",
            "id",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pack_id: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    granted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    remaining_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal(0), server_default="0"
    )
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class HostedCreditTopupSetting(Base):
    """Auto top-up preference. Absence of a row means the feature is off."""

    __tablename__ = "hosted_credit_topup_setting"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_hosted_credit_topup_account"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    threshold_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal("2.00"), server_default="2.00"
    )
    pack_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="hosted_credits_10",
        server_default="hosted_credits_10",
    )
    monthly_ceiling_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    period: Mapped[date | None] = mapped_column(Date, nullable=True)
    spent_this_period: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal(0), server_default="0"
    )
    disabled_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
