"""Portable back-office examples exercise the production policy matchers."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from preloop.models import models

from preloop.services.dynamic_fastmcp import _sensitive_denial_text
from preloop.services.model_content_policy import evaluate_model_io
from preloop.services.policy import load_policy_from_string
from preloop.services.policy.schema import SensitiveDataRule
from preloop.services.policy_simulation import PolicyEvaluationRequest, simulate_policy
from preloop.services.sensitive_data.policy_store import (
    parse_sensitive_data_config,
    serialize_sensitive_data_config,
)
from preloop.services.sensitive_data.tool_policy import evaluate_tool_target

PACK = Path(__file__).resolve().parents[3] / "docs/guide/policies/back-office-saas.yaml"


def test_pack_loads_and_round_trips() -> None:
    policy, validation = load_policy_from_string(PACK.read_text())
    assert validation.is_valid, validation.errors
    assert policy is not None
    assert policy.sensitive_data is not None
    serialized = serialize_sensitive_data_config(policy.sensitive_data)
    assert parse_sensitive_data_config(serialized, strict=True) == policy.sensitive_data
    assert len(policy.sensitive_data.rules) == 3
    assert "model.id" not in PACK.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "server,name,args,decision,rule",
    [
        ("unapproved", "read_record", {}, "notify", "back-office-unapproved-server"),
        (
            "crm",
            "update_record",
            {"id": "record-001"},
            "require_approval",
            "back-office-approve-writes",
        ),
        ("warehouse", "query", {"row_limit": 10001}, "deny", "back-office-bulk-export"),
        ("crm", "read_record", {"id": "record-001"}, "allow", None),
        (
            "warehouse",
            "delete_rows",
            {},
            "require_approval",
            "back-office-approve-writes",
        ),
        ("marketing", "read_campaign", {}, "allow", None),
        ("cdn-analytics", "query", {"limit": 10000}, "allow", None),
        (
            "warehouse",
            "query",
            {"sql": "SELECT * FROM customer_records"},
            "deny",
            "back-office-bulk-export",
        ),
        (
            "crm",
            "read_record",
            {"operation": "DOWNLOAD"},
            "deny",
            "back-office-bulk-export",
        ),
        ("warehouse", "export_rows", {}, "deny", "back-office-bulk-export"),
        ("unapproved", "download_records", {}, "deny", "back-office-bulk-export"),
        ("crm", "read_record", {"limit": "many"}, "deny", "back-office-bulk-export"),
    ],
)
async def test_tool_events(
    server: str, name: str, args: dict, decision: str, rule: str | None
) -> None:
    result = await simulate_policy(
        PolicyEvaluationRequest(
            name=name, server=server, args=args, draft_yaml=PACK.read_text()
        ),
        db=MagicMock(),
        account_id=uuid4(),
        user_id=uuid4(),
    )
    assert result.decision == decision
    assert result.matched_rule == rule
    if decision == "deny":
        assert "Bulk export blocked" in result.description


@pytest.mark.parametrize(
    "name,expected",
    [
        ("premium-unapproved", "notify"),
        ("premium-approved", "allow"),
        ("standard", "allow"),
    ],
)
def test_fifth_event_model_name(name: str, expected: str) -> None:
    policy, _ = load_policy_from_string(PACK.read_text())
    result = evaluate_model_io(
        rules=policy.model_io,
        ai_model=SimpleNamespace(id=uuid4(), model_identifier=name),
        target="model.request",
        text="Summarize this record",
        session_id="session-001",
        account_id=uuid4(),
        user_id=uuid4(),
        record=False,
    )
    assert result.action == expected


def test_live_notices_workflow_and_denial_message() -> None:
    policy, _ = load_policy_from_string(PACK.read_text())
    with (
        patch(
            "preloop.services.sensitive_data.tool_policy.schedule_policy_notice"
        ) as notice,
        patch("preloop.services.sensitive_data.tool_policy._audit_rule"),
    ):
        for server, name, expected in [
            ("unapproved", "read_record", "notify"),
            ("crm", "update_record", "require_approval"),
            ("warehouse", "download_rows", "deny"),
        ]:
            result = evaluate_tool_target(
                config=policy.sensitive_data,
                detector_config=None,
                target="tool.args",
                payload={},
                tool_name=name,
                server_name=server,
                managed_agent_id=None,
                account_id=uuid4(),
                user_id=uuid4(),
            )
            assert result.action == expected
            if expected == "require_approval":
                assert result.approval_workflow == "back-office-review"
            if expected == "deny":
                assert "Bulk export blocked" in _sensitive_denial_text(
                    result, where="tool arguments"
                )
        notice.assert_called_once()
        assert notice.call_args.args[0].rule_id == "back-office-unapproved-server"


@pytest.mark.parametrize(
    "overrides",
    [
        {"on": ["model.request"]},
        {"on": ["tool.result"]},
        {"types": ["email"]},
        {"action": "redact"},
        {"condition": "args.."},
    ],
)
def test_invalid_predicate_rules_are_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        SensitiveDataRule.model_validate(
            {
                "id": "example",
                "on": ["tool.args"],
                "action": "deny",
                "condition": "true",
                **overrides,
            }
        )


def test_import_pack_on_fresh_account(
    db_session: Session, test_user: models.User
) -> None:
    """The shipped pack has no dependency on pre-existing account resources."""
    from preloop.models.crud import crud_account, crud_approval_workflow
    from preloop.services.policy import export_current_policy
    from preloop.services.policy.loader import PolicyApplier

    account = crud_account.get(db_session, id=test_user.account_id)
    policy, validation = load_policy_from_string(PACK.read_text())
    assert validation.is_valid
    result = PolicyApplier(db_session, account.id, actor_id=test_user.id).apply(policy)
    assert result.success, result.errors
    workflow = crud_approval_workflow.get_by_name(
        db_session, account_id=str(account.id), name="back-office-review"
    )
    assert isinstance(workflow, models.ApprovalWorkflow)
    exported = export_current_policy(db_session, account.id)
    assert exported.sensitive_data == policy.sensitive_data
    assert exported.model_io == policy.model_io
