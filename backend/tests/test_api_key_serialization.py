"""Tenant locks and caller-owned transactions for credential lifecycle writes."""

from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from preloop.models import models
from preloop.models.crud import crud_api_key


def test_credential_lock_is_tenant_scoped_and_reloads_stale_state() -> None:
    db = MagicMock()
    key_id, tenant = uuid4(), uuid4()
    query = db.query.return_value
    filtered = query.filter.return_value
    locked = filtered.populate_existing.return_value.with_for_update.return_value
    row = locked.first.return_value
    assert crud_api_key.get_for_update(db, id=key_id, account_id=tenant) is row
    # Inspect SQL predicates: neither a global ID lookup nor an owner lookup
    # may accidentally replace the required account boundary.
    predicates = query.filter.call_args.args
    compiled = [clause.compile(dialect=postgresql.dialect()) for clause in predicates]
    assert "api_key.id" in str(compiled[0])
    assert "api_key.account_id" in str(compiled[1])
    assert list(compiled[0].params.values()) == [key_id]
    assert list(compiled[1].params.values()) == [tenant]
    filtered.populate_existing.assert_called_once()
    filtered.populate_existing.return_value.with_for_update.assert_called_once()
    db.commit.assert_not_called()


def test_runtime_update_retains_lock_until_atomic_audit_commit() -> None:
    db = MagicMock()
    row = models.ApiKey(name="synthetic-device", account_id=uuid4(), user_id=uuid4())
    crud_api_key.update(db, db_obj=row, obj_in={"is_active": False}, commit=False)
    assert row.is_active is False
    db.flush.assert_called_once()
    db.commit.assert_not_called()
    # Default updates still commit for existing callers.
    crud_api_key.update(db, db_obj=row, obj_in={"is_active": True})
    db.commit.assert_called_once()
