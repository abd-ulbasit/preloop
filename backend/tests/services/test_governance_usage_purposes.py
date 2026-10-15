"""Governance analysis spend must be readable through the purpose filter."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from preloop.models import models

from preloop.models.crud import crud_api_usage


@pytest.mark.parametrize("purpose", ["governance_session_digest", "governance_review"])
def test_governance_spend_purpose_is_accepted(purpose: str) -> None:
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.scalar.return_value = 0.05
    assert (
        crud_api_usage.get_gateway_spend(
            db,
            account_id=str(uuid4()),
            start=datetime.now(timezone.utc),
            purpose=purpose,
        )
        == 0.05
    )


def test_unknown_spend_purpose_still_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid gateway usage purpose"):
        crud_api_usage.get_gateway_spend(
            MagicMock(),
            account_id=str(uuid4()),
            start=datetime.now(timezone.utc),
            purpose="unknown",
        )


@pytest.mark.parametrize("funding", ["platform", "account"])
def test_governance_spend_is_scoped_by_funding(funding: str) -> None:
    from sqlalchemy.dialects import postgresql

    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.scalar.return_value = 0.03
    assert (
        crud_api_usage.get_gateway_spend(
            db,
            account_id=str(uuid4()),
            start=datetime.now(timezone.utc),
            purpose="governance_session_digest",
            funding=funding,
        )
        == 0.03
    )
    predicates = [
        str(
            call.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        for call in query.filter.call_args_list
    ]
    assert any(
        "funding" in predicate and funding in predicate for predicate in predicates
    )


def test_unknown_funding_still_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid gateway usage funding"):
        crud_api_usage.get_gateway_spend(
            MagicMock(),
            account_id=str(uuid4()),
            start=datetime.now(timezone.utc),
            funding="unknown",
        )


def test_funding_filter_is_restrictive_in_postgresql(
    db_session: Session,
    test_user: models.User,
) -> None:
    now = datetime.now(timezone.utc)
    for funding, cost in [
        ("platform", 0.01),
        ("account", 0.02),
        (None, 0.04),
        ("other", 0.08),
    ]:
        metadata = {"purpose": "governance_session_digest"}
        if funding is not None:
            metadata["funding"] = funding
        crud_api_usage.log_gateway_request(
            db_session,
            endpoint="test",
            method="POST",
            status_code=200,
            duration=0.1,
            account_id=str(test_user.account_id),
            estimated_cost=cost,
            meta_data=metadata,
        )
    for funding, expected in [("platform", 0.01), ("account", 0.02)]:
        assert crud_api_usage.get_gateway_spend(
            db_session,
            account_id=str(test_user.account_id),
            start=now - timedelta(minutes=1),
            purpose="governance_session_digest",
            funding=funding,
        ) == pytest.approx(expected)
