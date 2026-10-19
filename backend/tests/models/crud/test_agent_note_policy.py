"""Editable note defaults and human-specific approval recipient bindings."""

from uuid import uuid4

from preloop.models import models
from preloop.models.crud import crud_user
from preloop.models.crud.agent_note_policy import (
    caller_approval_workflow,
    seed_note_policy,
)


def test_seeding_preserves_existing_rules_and_edits(db_session, test_user):
    config = models.ToolConfiguration(
        account_id=test_user.account_id,
        tool_name="send_note",
        tool_source="builtin",
        is_enabled=True,
        custom_config={"existing": True},
    )
    db_session.add(config)
    db_session.flush()
    rule = models.ToolAccessRule(
        account_id=test_user.account_id,
        tool_configuration_id=config.id,
        action="deny",
        priority=1,
        description="Existing restriction",
    )
    db_session.add(rule)
    db_session.flush()
    assert seed_note_policy(db_session, account_id=test_user.account_id)
    rules = (
        db_session.query(models.ToolAccessRule)
        .filter_by(tool_configuration_id=config.id)
        .all()
    )
    assert len(rules) == 4
    assert config.is_enabled
    assert config.custom_config["existing"]
    assert rule.priority == 1
    seeded = next(item for item in rules if item.action == "allow")
    seeded.is_enabled = False
    db_session.flush()
    assert not seed_note_policy(db_session, account_id=test_user.account_id)
    assert not seeded.is_enabled
    assert (
        db_session.query(models.ToolAccessRule)
        .filter_by(tool_configuration_id=config.id)
        .count()
        == 4
    )


def test_caller_workflows_keep_recipients_separate_and_follow_template(
    db_session, test_user
):
    account_id = test_user.account_id
    seed_note_policy(db_session, account_id=account_id)
    template = (
        db_session.query(models.ApprovalWorkflow)
        .filter_by(account_id=account_id, name="Note sender approval")
        .one()
    )
    other = crud_user.create(
        db_session,
        obj_in={
            "account_id": account_id,
            "email": "other@example.com",
            "username": f"other-{uuid4()}",
            "hashed_password": "unused",
            "is_active": True,
        },
    )
    first_id = caller_approval_workflow(
        db_session, account_id=account_id, workflow_id=template.id, user_id=test_user.id
    )
    other_id = caller_approval_workflow(
        db_session, account_id=account_id, workflow_id=template.id, user_id=other.id
    )
    assert first_id != other_id
    first = db_session.get(models.ApprovalWorkflow, first_id)
    second = db_session.get(models.ApprovalWorkflow, other_id)
    assert first.approver_user_ids == [test_user.id]
    assert second.approver_user_ids == [other.id]
    assert not template.approver_user_ids
    template.async_approval_enabled = False
    template.timeout_seconds = 120
    db_session.flush()
    assert (
        caller_approval_workflow(
            db_session,
            account_id=account_id,
            workflow_id=template.id,
            user_id=test_user.id,
        )
        == first_id
    )
    assert first.async_approval_enabled is False
    assert first.timeout_seconds == 120
    assert (
        caller_approval_workflow(
            db_session, account_id=account_id, workflow_id=template.id, user_id=None
        )
        is None
    )
