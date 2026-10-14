"""Deny and approval matches are stored as policy hits (#1710)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from preloop.services import model_content_policy as mcp
from preloop.services.model_content_policy import evaluate_model_io
from preloop.services.policy.schema import ModelIORule, ToolCondition

ACCOUNT = uuid4()
USER = uuid4()
SESSION = uuid4()


def _deny_rule() -> ModelIORule:
    return ModelIORule.model_validate(
        {
            "id": "deny-secret",
            "target": "model.request",
            "conditions": [
                ToolCondition(
                    expression="request.text.contains('project-x')",
                    action="deny",
                )
            ],
        }
    )


def _approval_rule() -> ModelIORule:
    return ModelIORule.model_validate(
        {
            "id": "hold-secret",
            "target": "model.request",
            "approval_workflow": "Default",
            "conditions": [
                ToolCondition(
                    expression="request.text.contains('project-x')",
                    action="require_approval",
                )
            ],
        }
    )


class _FakeDb:
    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def in_transaction(self) -> bool:
        return False

    def invalidate(self) -> None:
        return None


def _fake_db_session():
    yield _FakeDb()


def test_deny_match_records_one_hit_with_excerpt_and_audit_id() -> None:
    text = ("alpha " * 80) + "project-x " + ("omega " * 80)
    hits = []
    audits = []
    with (
        patch.object(mcp, "schedule_policy_match_hit", side_effect=hits.append),
        patch.object(
            mcp,
            "_log_policy_decision_async",
            side_effect=lambda **kwargs: audits.append(kwargs),
        ),
    ):
        decision = evaluate_model_io(
            rules=[_deny_rule()],
            target="model.request",
            text=text,
            account_id=ACCOUNT,
            user_id=USER,
            ai_model=SimpleNamespace(model_identifier="flash-test"),
            model_alias="flash-test",
            agent_ref="codex:agent-1",
            runtime_session_id=SESSION,
        )

    assert decision.action == "deny"
    assert len(hits) == 1
    hit = hits[0]
    assert hit.action == "deny"
    assert hit.rule_id == "deny-secret"
    assert hit.excerpt is not None
    assert len(hit.excerpt) <= 280
    assert "project-x" in hit.excerpt
    assert hit.audit_event_id is not None
    assert hit.model_alias == "flash-test"
    assert hit.agent_ref == "codex:agent-1"
    assert hit.runtime_session_id == SESSION
    assert audits[0]["extra_details"]["audit_event_id"] == str(hit.audit_event_id)
    assert audits[0]["tool_args"] == {"text_sha256": hit.text_sha256}
    assert "project-x" not in hit.text_sha256


def test_require_approval_match_records_one_hit() -> None:
    hits = []
    with (
        patch.object(mcp, "schedule_policy_match_hit", side_effect=hits.append),
        patch.object(mcp, "_log_policy_decision_async"),
    ):
        decision = evaluate_model_io(
            rules=[_approval_rule()],
            target="model.request",
            text="project-x",
            account_id=ACCOUNT,
            user_id=USER,
        )
    assert decision.action == "require_approval"
    assert len(hits) == 1
    assert hits[0].action == "require_approval"
    assert hits[0].audit_event_id is not None


def test_failing_hit_write_does_not_change_the_decision() -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    with (
        patch(
            "preloop.services.db_executor.submit_off_loop",
            side_effect=lambda operation: operation(),
        ),
        patch("preloop.models.db.session.get_db_session", _fake_db_session),
        patch(
            "preloop.services.policy_notices.crud_policy_notice_hit.record",
            side_effect=_boom,
        ),
        patch.object(mcp, "_log_policy_decision_async"),
    ):
        decision = evaluate_model_io(
            rules=[_deny_rule()],
            target="model.request",
            text="project-x",
            account_id=ACCOUNT,
            user_id=USER,
        )
    assert decision.action == "deny"
    assert decision.rule_id == "deny-secret"


def test_notify_hit_carries_model_agent_and_session() -> None:
    rule = ModelIORule.model_validate(
        {
            "id": "notify-secret",
            "target": "model.request",
            "conditions": [
                ToolCondition(
                    expression="request.text.contains('project-x')",
                    action="notify",
                )
            ],
        }
    )
    notices = []
    with (
        patch.object(mcp, "schedule_policy_notice", side_effect=notices.append),
        patch.object(mcp, "_log_policy_decision_async") as audit,
    ):
        decision = evaluate_model_io(
            rules=[rule],
            target="model.request",
            text="project-x",
            account_id=ACCOUNT,
            user_id=USER,
            model_alias="small-test",
            agent_ref="claude:dev-1",
            runtime_session_id=SESSION,
        )
    assert decision.action == "notify"
    assert len(notices) == 1
    notice = notices[0]
    assert notice.action == "notify"
    assert notice.model_alias == "small-test"
    assert notice.agent_ref == "claude:dev-1"
    assert notice.runtime_session_id == SESSION
    assert notice.audit_event_id is not None
    assert audit.call_args.kwargs["extra_details"]["audit_event_id"] == str(
        notice.audit_event_id
    )


def test_agent_ref_uses_runtime_principal() -> None:
    gateway = SimpleNamespace(
        auth_context=SimpleNamespace(
            api_key=SimpleNamespace(
                context_data={"runtime_principal": {"type": "Codex", "id": "agent-9"}}
            )
        )
    )
    assert mcp._agent_ref_from_gateway(gateway) == "codex:agent-9"
