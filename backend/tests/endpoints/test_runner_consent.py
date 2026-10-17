"""Runner consent is owner-controlled and preserved across registrations."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from preloop.api.endpoints import runners
from preloop.models import schemas


def test_registration_consent_is_optional_and_validated() -> None:
    assert schemas.RunnerRegisterRequest().flow_execution_consent is None
    for mode in ("always", "ask_me", "never"):
        assert (
            schemas.RunnerRegisterRequest(
                flow_execution_consent=mode
            ).flow_execution_consent
            == mode
        )
    with pytest.raises(ValidationError):
        schemas.RunnerRegisterRequest(flow_execution_consent="allow")


@pytest.mark.parametrize("mode", ["always", "ask_me", "never"])
def test_only_owner_can_change_runner_consent(monkeypatch, mode) -> None:
    owner, outsider, account, runner_id = uuid4(), uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        id=runner_id,
        registered_by_user_id=owner,
        account_id=account,
        flow_execution_consent="ask_me",
    )
    monkeypatch.setattr(runners.crud_flow_runner, "get", lambda *a, **k: row)
    update = MagicMock(side_effect=lambda db, **k: row)
    monkeypatch.setattr(runners.crud_flow_runner, "update", update)
    audit = MagicMock()
    monkeypatch.setattr(runners.crud_audit_log, "log_action", audit)
    monkeypatch.setattr(runners, "emit_runner_updated", lambda *a: None)
    monkeypatch.setattr(runners, "_to_response", lambda *a: a[0])
    body = schemas.RunnerConsentUpdate(flow_execution_consent=mode)
    with pytest.raises(HTTPException) as exc:
        runners.update_runner_consent(
            runner_id,
            body,
            MagicMock(),
            SimpleNamespace(id=outsider, account_id=account),
        )
    assert exc.value.status_code == 403
    update.assert_not_called()
    runners.update_runner_consent(
        runner_id, body, MagicMock(), SimpleNamespace(id=owner, account_id=account)
    )
    assert update.call_args.kwargs["obj_in"] == {"flow_execution_consent": mode}
    audit.assert_called_once()
