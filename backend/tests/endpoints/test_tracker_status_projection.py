"""Authorization and sync validation remain independent in list/detail payloads."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from preloop.api.endpoints.trackers import _tracker_responses
from preloop.models import models


@pytest.mark.parametrize(
    "provider,binding,required",
    [
        ("jira", "cloud_id", "site_required"),
        ("bitbucket", "workspace", "workspace_required"),
    ],
)
@pytest.mark.parametrize(
    "grant,configured,bound,expected",
    [
        ("active", True, True, "connected"),
        ("active", True, False, "required"),
        ("invalidated", True, True, "reconnect_required"),
        ("disconnected", True, True, "disconnected"),
        ("active", False, True, "unavailable"),
        (None, True, True, "reconnect_required"),
    ],
)
@pytest.mark.parametrize(
    "valid,checked,message",
    [
        (False, None, None),
        (True, datetime(2026, 1, 1, tzinfo=timezone.utc), None),
        (False, datetime(2026, 1, 1, tzinfo=timezone.utc), "Sync failed"),
    ],
)
def test_authorization_projection(
    provider: str,
    binding: str,
    required: str,
    grant: str | None,
    configured: bool,
    bound: bool,
    expected: str,
    valid: bool,
    checked: datetime | None,
    message: str | None,
) -> None:
    account_id = uuid4()
    tracker = models.Tracker(
        id=uuid4(),
        account_id=account_id,
        name="Example tracker",
        tracker_type=provider,
        is_active=True,
        auth_type="managed_oauth",
        is_valid=valid,
        last_validation=checked,
        validation_message=message,
        created=datetime.now(timezone.utc),
        last_updated=datetime.now(timezone.utc),
        connection_details={
            **({binding: "example"} if bound else {}),
            "managed_state": "disconnected" if grant == "disconnected" else "connected",
        },
    )
    with (
        patch(
            "preloop.api.endpoints.trackers.crud_managed_oauth.grant_statuses",
            return_value={tracker.id: grant} if grant else {},
        ) as batch,
        patch(
            "preloop.api.endpoints.trackers.get_plugin_manager",
            return_value=MagicMock(
                get_enabled_features=lambda: {
                    "features": {f"{provider}_cloud_oauth": True} if configured else {}
                }
            ),
        ),
    ):
        payload = _tracker_responses(MagicMock(), [tracker])[0]
    assert payload.authorization_state == (
        required if expected == "required" else expected
    )
    assert payload.is_valid is valid
    assert payload.last_validation == checked
    assert payload.validation_message == message
    assert batch.call_count == 1
    assert batch.call_args.kwargs["account_id"] == account_id


def test_manual_tracker_does_not_query_grants() -> None:
    now = datetime.now(timezone.utc)
    tracker = models.Tracker(
        id=uuid4(),
        account_id=uuid4(),
        name="Example tracker",
        tracker_type="jira",
        is_active=True,
        auth_type="api_token",
        is_valid=False,
        created=now,
        last_updated=now,
    )
    with patch(
        "preloop.api.endpoints.trackers.crud_managed_oauth.grant_statuses"
    ) as batch:
        payload = _tracker_responses(MagicMock(), [tracker])[0]
    assert payload.authorization_state is None
    batch.assert_not_called()


def test_grant_status_batch_is_account_scoped_and_reads_no_credentials() -> None:
    from preloop.models.crud.managed_oauth import CRUDManagedOAuth

    db = MagicMock()
    account_id, tracker_id = uuid4(), uuid4()
    db.execute.return_value = [(tracker_id, "active")]
    crud = CRUDManagedOAuth()
    assert crud.grant_statuses(db, account_id=account_id, tracker_ids=[]) == {}
    db.execute.assert_not_called()
    assert crud.grant_statuses(db, account_id=account_id, tracker_ids=[tracker_id]) == {
        tracker_id: "active"
    }
    db.execute.assert_called_once()
    statement = db.execute.call_args.args[0]
    compiled = statement.compile()
    assert account_id in compiled.params.values()
    assert [tracker_id] in compiled.params.values()
    assert "managed_oauth" in compiled.params.values()
    assert [column.name for column in statement.selected_columns] == [
        "tracker_id",
        "status",
    ]
