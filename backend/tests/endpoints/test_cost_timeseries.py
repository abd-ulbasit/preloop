"""The spend chart endpoint buckets gateway spend and refuses unbounded windows."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from preloop.models.crud import crud_api_usage


def _log(db_session: Any, test_user: Any, when: datetime, cost: float) -> None:
    usage = crud_api_usage.log_gateway_request(
        db_session,
        endpoint="/openai/v1/responses",
        method="POST",
        status_code=200,
        duration=0.1,
        account_id=str(test_user.account_id),
        user_id=str(test_user.id),
        model_alias="example-model",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        estimated_cost=cost,
    )
    usage.timestamp = when
    db_session.commit()


@pytest.mark.parametrize(
    ("granularity", "expected"),
    [
        ("day", {"2026-03-02": 1.0, "2026-03-04": 2.0, "2026-04-01": 4.0}),
        ("week", {"2026-03-02": 3.0, "2026-03-30": 4.0}),
        ("month", {"2026-03-01": 3.0, "2026-04-01": 4.0}),
    ],
)
def test_timeseries_buckets_by_granularity(
    client: Any,
    db_session: Any,
    test_user: Any,
    granularity: str,
    expected: dict[str, float],
) -> None:
    """Each bucket is keyed by its UTC start and sums the rows inside it."""
    _log(db_session, test_user, datetime(2026, 3, 2, 10, tzinfo=UTC), 1.0)
    _log(db_session, test_user, datetime(2026, 3, 4, 10, tzinfo=UTC), 2.0)
    _log(db_session, test_user, datetime(2026, 4, 1, 10, tzinfo=UTC), 4.0)
    response = client.get(
        "/api/v1/cost/timeseries",
        params={
            "start_date": "2026-03-01T00:00:00Z",
            "end_date": "2026-04-30T00:00:00Z",
            "granularity": granularity,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["granularity"] == granularity
    got = {row["date"]: row["estimated_cost"] for row in body["buckets"]}
    assert got == pytest.approx(expected)


def test_timeseries_rejects_windows_over_a_year(client: Any) -> None:
    """One request must never scan unbounded history."""
    end = datetime(2026, 6, 1, tzinfo=UTC)
    response = client.get(
        "/api/v1/cost/timeseries",
        params={
            "start_date": (end - timedelta(days=367)).isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert response.status_code == 422


def test_timeseries_rejects_unknown_granularity(client: Any) -> None:
    """Only day, week and month are buckets."""
    response = client.get("/api/v1/cost/timeseries?granularity=hour")
    assert response.status_code == 422
