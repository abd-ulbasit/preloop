"""Governance analysis spend must be readable through the purpose filter."""

from unittest.mock import MagicMock
from uuid import uuid4
from datetime import datetime, timezone

import pytest

from preloop.models.crud import crud_api_usage


@pytest.mark.parametrize("purpose", ["governance_session_digest", "governance_review"])
def test_governance_spend_purpose_is_accepted(purpose: str) -> None:
    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.scalar.return_value = 0.05
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
