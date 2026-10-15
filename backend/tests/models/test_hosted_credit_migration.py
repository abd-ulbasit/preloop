"""Credit migration creates the lot ledger and downgrades an empty schema."""

import importlib.util
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from preloop.models import models


def load_migration() -> Any:
    path = (
        Path(__file__).resolve().parents[2]
        / "preloop/models/alembic/versions/20261018_hosted_credit.py"
    )
    spec = importlib.util.spec_from_file_location("hosted_credit_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_and_downgrade_on_an_empty_credit_schema(db_session: Any) -> None:
    account = models.Account(id=uuid4(), organization_name="credit-migration")
    db_session.add(account)
    db_session.flush()
    migration = load_migration()
    connection = db_session.connection()
    with Operations.context(MigrationContext.configure(connection)):
        if "hosted_credit_account" in inspect(connection).get_table_names():
            migration.downgrade()
        migration.upgrade()
    for model in (
        models.HostedCreditAccount,
        models.HostedCreditPurchase,
        models.HostedCreditLedger,
        models.HostedCreditTopupSetting,
    ):
        actual = {
            column["name"]
            for column in inspect(connection).get_columns(model.__tablename__)
        }
        assert actual == {column.name for column in model.__table__.columns}
        assert db_session.execute(select(model.id)).first() is None
    reservation = {
        column["name"]
        for column in inspect(connection).get_columns("hosted_spend_reservation")
    }
    assert {"credit_reserved", "price_multiplier"} <= reservation
    checks = {
        item["name"]
        for item in inspect(connection).get_check_constraints("hosted_credit_account")
    }
    assert "ck_hosted_credit_account_balance" in checks
    assert "ck_hosted_credit_account_reserved" in checks
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    INSERT INTO hosted_credit_account (
                        id, account_id, purchased, consumed, reserved,
                        refunded, expired, balance, created_at, updated_at
                    ) VALUES (
                        :id, :account_id, 0, 0, 0, 0, 0, 1, now(), now()
                    )
                    """
                ),
                {"id": uuid4(), "account_id": account.id},
            )
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
    tables = set(inspect(connection).get_table_names())
    assert "hosted_credit_account" not in tables
    assert "hosted_credit_ledger" not in tables
    assert "hosted_credit_purchase" not in tables
    assert "hosted_credit_topup_setting" not in tables
    reservation = {
        column["name"]
        for column in inspect(connection).get_columns("hosted_spend_reservation")
    }
    assert "credit_reserved" not in reservation
    assert "price_multiplier" not in reservation
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
