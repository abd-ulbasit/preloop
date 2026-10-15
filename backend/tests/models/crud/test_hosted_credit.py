"""Prepaid credit lots: FIFO, expiry, idempotency, and the account lock."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import hosted_credit as credit
from preloop.models.crud import hosted_spend as allowance
from preloop.models.crud.hosted_spend import (
    HostedSpendExceededError,
    HostedSpendUnavailableError,
)

NOW = datetime(2026, 1, 15, tzinfo=UTC)
ACTOR = "operator"


@pytest.fixture
def account_id(db_engine: Any) -> Any:
    identifier = uuid4()
    with Session(db_engine) as db:
        db.add(models.Account(id=identifier, organization_name="credit-ledger-test"))
        db.commit()
    yield identifier
    with Session(db_engine) as db:
        for model in (
            models.HostedCreditLedger,
            models.HostedCreditPurchase,
            models.HostedCreditAccount,
            models.HostedCreditTopupSetting,
            models.HostedSpendReservation,
            models.HostedSpendMonth,
            models.HostedSpendAccount,
        ):
            db.execute(delete(model).where(model.account_id == identifier))
        db.execute(delete(models.Account).where(models.Account.id == identifier))
        db.commit()


def _grant(
    db: Session,
    account_id: UUID,
    amount: Any = "10",
    key: str | None = None,
    kind: str = "purchase",
    **kwargs: Any,
) -> models.HostedCreditLedger:
    return credit.grant(
        db,
        account_id=account_id,
        amount=amount,
        operation_key=key or str(uuid4()),
        kind=kind,
        actor=ACTOR,
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


def _remaining(db: Session, account_id: UUID) -> Decimal:
    total = db.scalar(
        select(
            func.coalesce(func.sum(models.HostedCreditPurchase.remaining_usd), 0)
        ).where(models.HostedCreditPurchase.account_id == account_id)
    )
    assert total is not None
    return Decimal(total)


def _assert_lots_match_balance(db: Session, account_id: UUID) -> None:
    row = db.scalar(
        select(models.HostedCreditAccount).where(
            models.HostedCreditAccount.account_id == account_id
        )
    )
    assert row is not None
    if row.balance >= 0:
        assert _remaining(db, account_id) == row.balance


def test_balance_without_a_row_is_zero_and_writes_nothing(
    db_engine: Any, account_id: UUID
) -> None:
    with Session(db_engine) as db:
        snapshot = credit.balance(db, account_id=account_id)
        assert snapshot == {
            "purchased": Decimal(0),
            "consumed": Decimal(0),
            "reserved": Decimal(0),
            "refunded": Decimal(0),
            "expired": Decimal(0),
            "balance": Decimal(0),
            "available": Decimal(0),
            "next_expiring_amount": Decimal(0),
            "next_expires_at": None,
        }
        assert credit.entries(db, account_id=account_id) == []
        assert (
            db.scalar(
                select(func.count())
                .select_from(models.HostedCreditAccount)
                .where(models.HostedCreditAccount.account_id == account_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(models.HostedCreditTopupSetting)
                .where(models.HostedCreditTopupSetting.account_id == account_id)
            )
            == 0
        )


@pytest.mark.parametrize(
    "amount",
    [-1, "-1", 0, "0", Decimal("0"), "nan", "inf", "nope", None],
)
def test_non_positive_and_non_numeric_amounts_are_refused(
    db_engine: Any, account_id: UUID, amount: Any
) -> None:
    with Session(db_engine) as db:
        with pytest.raises(HostedSpendUnavailableError):
            _grant(db, account_id, amount=amount)
        db.rollback()
        assert credit.balance(db, account_id=account_id)["balance"] == 0


def test_naive_datetime_is_refused(db_engine: Any, account_id: UUID) -> None:
    with Session(db_engine) as db:
        with pytest.raises(HostedSpendUnavailableError, match="aware"):
            _grant(db, account_id, now=datetime(2026, 1, 15))
        db.rollback()
        assert credit.balance(db, account_id=account_id)["balance"] == 0


def test_grant_is_idempotent_and_starts_a_twelve_month_clock(
    db_engine: Any, account_id: UUID
) -> None:
    with Session(db_engine) as db:
        first = _grant(db, account_id, key="grant-1", note="pack")
        second = _grant(db, account_id, key="grant-1", note="pack")
        db.commit()
        assert first.id == second.id
        snapshot = credit.balance(db, account_id=account_id)
        assert snapshot["purchased"] == Decimal("10")
        assert snapshot["balance"] == Decimal("10")
        assert snapshot["available"] == Decimal("10")
        assert snapshot["next_expiring_amount"] == Decimal("10")
        assert snapshot["next_expires_at"] == datetime(2027, 1, 15, tzinfo=UTC)
        assert len(credit.entries(db, account_id=account_id)) == 1
        _assert_lots_match_balance(db, account_id)
        with pytest.raises(HostedSpendUnavailableError, match="Conflicting"):
            _grant(db, account_id, amount="9", key="grant-1")


def test_grant_refuses_to_replay_a_hold_that_reused_the_key(
    db_engine: Any, account_id: UUID
) -> None:
    with Session(db_engine) as db:
        _grant(db, account_id, amount="10", key="pool")
        credit.hold(
            db,
            account_id=account_id,
            amount="4",
            operation_key="shared",
            actor=ACTOR,
        )
        db.commit()
        before = credit.balance(db, account_id=account_id)
        with pytest.raises(HostedSpendUnavailableError, match="Conflicting"):
            credit.grant(
                db,
                account_id=account_id,
                amount="4",
                operation_key="shared",
                kind="adjustment",
                actor=ACTOR,
                now=NOW,
            )
        db.rollback()
        after = credit.balance(db, account_id=account_id)
        assert after["purchased"] == before["purchased"] == Decimal("10")
        assert after["reserved"] == Decimal("4")


def test_grant_clears_a_negative_balance_before_the_lot_is_spendable(
    db_engine: Any, account_id: UUID
) -> None:
    with Session(db_engine) as db:
        db.add(
            models.HostedCreditAccount(
                account_id=account_id,
                purchased=Decimal(0),
                consumed=Decimal("3"),
                reserved=Decimal(0),
                refunded=Decimal(0),
                expired=Decimal(0),
                balance=Decimal("-3"),
            )
        )
        db.commit()
        entry = _grant(db, account_id, amount="10", key="cover-deficit")
        db.commit()
        assert entry.amount == Decimal("10")
        assert entry.kind == "purchase"
        snapshot = credit.balance(db, account_id=account_id)
        assert snapshot["balance"] == Decimal("7")
        assert snapshot["available"] == Decimal("7")
        assert _remaining(db, account_id) == Decimal("7")
        assert (
            db.scalar(
                select(func.count())
                .select_from(models.HostedCreditLedger)
                .where(models.HostedCreditLedger.account_id == account_id)
            )
            == 1
        )


def test_debit_spends_oldest_lots_and_breaks_equal_clocks_on_id(
    db_engine: Any, account_id: UUID
) -> None:
    with Session(db_engine) as db:
        for offset, amount in ((0, "3"), (1, "5"), (2, "7")):
            _grant(
                db,
                account_id,
                amount=amount,
                key=f"lot-{offset}",
                now=NOW + timedelta(days=offset),
            )
        entries = credit.debit(
            db,
            account_id=account_id,
            amount="4",
            operation_key="spend-span",
            actor=ACTOR,
        )
        db.commit()
        assert [entry.amount for entry in entries] == [Decimal("3"), Decimal("1")]
        assert len({entry.purchase_id for entry in entries}) == 2
        lots = list(
            db.scalars(
                select(models.HostedCreditPurchase)
                .where(models.HostedCreditPurchase.account_id == account_id)
                .order_by(models.HostedCreditPurchase.granted_at)
            )
        )
        assert lots[0].remaining_usd == 0
        assert lots[1].remaining_usd == Decimal("4")
        assert lots[2].remaining_usd == Decimal("7")
        _assert_lots_match_balance(db, account_id)
        again = credit.debit(
            db,
            account_id=account_id,
            amount="4",
            operation_key="spend-span",
            actor=ACTOR,
        )
        assert [entry.id for entry in again] == [entry.id for entry in entries]


def test_equal_granted_at_breaks_on_id(db_engine: Any, account_id: UUID) -> None:
    same_clock = NOW + timedelta(days=10)
    low = UUID("00000000-0000-4000-8000-000000000001")
    high = UUID("00000000-0000-4000-8000-000000000002")
    with Session(db_engine) as db:
        for purchase_id in (high, low):
            db.add(
                models.HostedCreditPurchase(
                    id=purchase_id,
                    account_id=account_id,
                    pack_id="hosted_credits_10",
                    amount_usd=Decimal("3"),
                    source="checkout",
                    status="pending",
                    remaining_usd=Decimal(0),
                    granted_at=same_clock,
                )
            )
        db.flush()
        _grant(
            db, account_id, amount="3", key="tie-high", purchase_id=high, now=same_clock
        )
        _grant(
            db, account_id, amount="3", key="tie-low", purchase_id=low, now=same_clock
        )
        first = credit.debit(
            db,
            account_id=account_id,
            amount="3",
            operation_key="tie-1",
            actor=ACTOR,
        )[0]
        second = credit.debit(
            db,
            account_id=account_id,
            amount="3",
            operation_key="tie-2",
            actor=ACTOR,
        )[0]
        db.commit()
        assert first.purchase_id == low
        assert second.purchase_id == high
        _assert_lots_match_balance(db, account_id)


def test_hold_does_not_spend_allowance_and_blocks_debit_and_expiry(
    db_engine: Any, account_id: UUID
) -> None:
    with Session(db_engine) as db:
        allowance.establish_baseline(
            db,
            account_id=account_id,
            lifetime_spent="0",
            month_spent="0",
            now=NOW,
            evidence="isolated test account creation",
        )
        reservation = allowance.reserve(
            db,
            account_id=account_id,
            operation_key="allowance-1",
            amount="0.1",
            lifetime_limit="1",
            monthly_limit=None,
            now=NOW,
        )
        _grant(db, account_id, amount="5", key="older", now=NOW)
        _grant(db, account_id, amount="5", key="newer", now=NOW + timedelta(days=1))
        older, newer = list(
            db.scalars(
                select(models.HostedCreditPurchase)
                .where(models.HostedCreditPurchase.account_id == account_id)
                .order_by(models.HostedCreditPurchase.granted_at)
            )
        )
        held = credit.hold(
            db,
            account_id=account_id,
            amount="5",
            operation_key="hold-1",
            actor=ACTOR,
            reservation_id=reservation.id,
            price_multiplier="1.5",
        )
        db.commit()
        assert held.note == "credit-hold"
        assert reservation.reserved == Decimal("0.1")
        assert reservation.credit_reserved == Decimal("5")
        assert reservation.price_multiplier == Decimal("1.5000")
        assert credit.balance(db, account_id=account_id)["available"] == Decimal("5")
        spent = credit.debit(
            db,
            account_id=account_id,
            amount="5",
            operation_key="spend-free",
            actor=ACTOR,
        )
        db.commit()
        assert spent[0].purchase_id == newer.id
        assert newer.remaining_usd == 0
        assert older.remaining_usd == Decimal("5")
        assert credit.expire(db, purchase_id=older.id) is None
        assert older.remaining_usd == Decimal("5")
        released = credit.release(
            db, account_id=account_id, operation_key="hold-1", actor=ACTOR
        )
        again = credit.release(
            db, account_id=account_id, operation_key="hold-1", actor=ACTOR
        )
        db.commit()
        assert released.id == again.id
        assert reservation.credit_reserved == 0
        assert reservation.reserved == Decimal("0.1")
        expired = credit.expire(db, purchase_id=older.id)
        repeat = credit.expire(db, purchase_id=older.id)
        db.commit()
        assert expired is not None
        assert repeat is not None
        assert expired.id == repeat.id
        assert expired.kind == "expiry"
        assert expired.operation_key == f"credit-expiry:{older.id}"
        assert expired.amount == Decimal("5")
        assert older.status == "expired"
        assert older.remaining_usd == 0
        assert older.expired_at is not None
        row = db.scalar(
            select(models.HostedCreditAccount).where(
                models.HostedCreditAccount.account_id == account_id
            )
        )
        expiry_total = db.scalar(
            select(func.coalesce(func.sum(models.HostedCreditLedger.amount), 0)).where(
                models.HostedCreditLedger.account_id == account_id,
                models.HostedCreditLedger.kind == "expiry",
            )
        )
        assert row is not None
        assert expiry_total is not None
        assert row.expired == Decimal(expiry_total) == Decimal("5")
        assert (
            db.scalar(
                select(func.count())
                .select_from(models.HostedCreditLedger)
                .where(
                    models.HostedCreditLedger.account_id == account_id,
                    models.HostedCreditLedger.kind == "expiry",
                )
            )
            == 1
        )
        _assert_lots_match_balance(db, account_id)
        assert credit.recompute(db, account_id=account_id)["status"] == "ok"
        assert (
            db.scalar(
                select(func.count())
                .select_from(models.HostedCreditTopupSetting)
                .where(models.HostedCreditTopupSetting.account_id == account_id)
            )
            == 0
        )


def test_partial_hold_is_not_expired(db_engine: Any, account_id: UUID) -> None:
    with Session(db_engine) as db:
        granted = _grant(db, account_id, amount="10", key="partial-lot")
        credit.hold(
            db,
            account_id=account_id,
            amount="4",
            operation_key="partial-hold",
            actor=ACTOR,
        )
        db.commit()
        assert credit.expire(db, purchase_id=granted.purchase_id) is None
        lot = db.get(models.HostedCreditPurchase, granted.purchase_id)
        assert lot is not None
        assert lot.remaining_usd == Decimal("10")
        assert lot.status == "granted"
        assert (
            db.scalar(
                select(func.count())
                .select_from(models.HostedCreditLedger)
                .where(
                    models.HostedCreditLedger.account_id == account_id,
                    models.HostedCreditLedger.kind == "expiry",
                )
            )
            == 0
        )


def test_two_concurrent_holds_cannot_oversell(db_engine: Any, account_id: UUID) -> None:
    with Session(db_engine) as db:
        _grant(db, account_id, amount="5", key="pool")
        db.commit()
    barrier = Barrier(2)

    def attempt() -> str:
        with Session(db_engine) as db:
            barrier.wait(timeout=5)
            try:
                credit.hold(
                    db,
                    account_id=account_id,
                    amount="5",
                    operation_key=str(uuid4()),
                    actor=ACTOR,
                )
                db.commit()
                return "reserved"
            except HostedSpendExceededError:
                db.rollback()
                return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["denied", "reserved"]
    with Session(db_engine) as db:
        assert credit.balance(db, account_id=account_id)["reserved"] == Decimal("5")
        assert credit.balance(db, account_id=account_id)["available"] == 0


def test_recompute_records_an_adjustment_and_leaves_history(
    db_engine: Any, account_id: UUID
) -> None:
    with Session(db_engine) as db:
        original = _grant(db, account_id, key="original")
        original_id = original.id
        original_amount = original.amount
        db.commit()
        row = db.scalar(
            select(models.HostedCreditAccount).where(
                models.HostedCreditAccount.account_id == account_id
            )
        )
        assert row is not None
        row.purchased += Decimal("1")
        row.balance += Decimal("1")
        db.commit()
        preview = credit.recompute(db, account_id=account_id)
        assert preview["status"] == "drift"
        assert Decimal(preview["drift"]["purchased"]) == Decimal("1")
        assert Decimal(preview["drift"]["balance"]) == Decimal("1")
        applied = credit.recompute(db, account_id=account_id, apply=True)
        db.commit()
        assert applied["status"] == "adjusted"
        assert credit.recompute(db, account_id=account_id)["status"] == "ok"
        history = db.get(models.HostedCreditLedger, original_id)
        assert history is not None
        assert history.amount == original_amount
        assert history.kind == "purchase"
        adjustment = db.get(models.HostedCreditLedger, applied["adjustment_id"])
        assert adjustment is not None
        assert adjustment.kind == "adjustment"
        assert adjustment.note is not None
        assert adjustment.note.startswith("recompute:")
        assert credit.balance(db, account_id=account_id)["balance"] == Decimal("10")
        page = credit.entries(db, account_id=account_id, limit=1)
        assert len(page) == 1
        rest = credit.entries(db, account_id=account_id, after_id=page[0]["id"])
        assert page[0]["id"] not in {item["id"] for item in rest}
        with pytest.raises(ValueError):
            credit.entries(db, account_id=account_id, limit=0)


def test_entries_page_is_bounded(db_engine: Any, account_id: UUID) -> None:
    with Session(db_engine) as db:
        _grant(db, account_id, key="page-a", amount="1")
        _grant(db, account_id, key="page-b", amount="1", now=NOW + timedelta(days=1))
        db.commit()
        page = credit.entries(db, account_id=account_id, limit=1)
        assert len(page) == 1
        assert credit.balance(db, account_id=account_id)["purchased"] == Decimal("2")
        _assert_lots_match_balance(db, account_id)
