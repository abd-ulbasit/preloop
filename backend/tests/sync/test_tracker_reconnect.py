"""Explicit reconnects must discover projects without stealing tracker scope."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_account
from preloop.sync import tasks
from preloop.sync.scanner.core import TrackerClient


@pytest.mark.asyncio
async def test_on_demand_defaults_to_forced_discovery() -> None:
    with patch.object(tasks, "poll_tracker", AsyncMock(return_value={})) as poll:
        await tasks.poll_tracker_on_demand("tracker-id")
    poll.assert_awaited_once_with("tracker-id", None, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_account", [True, False])
@pytest.mark.parametrize("tracker_type", ["bitbucket", "jira"])
@pytest.mark.parametrize("old_deleted", [True, False])
async def test_reconnect_does_not_reparent_recent_organization(
    db_session: Session,
    test_user: models.User,
    same_account: bool,
    tracker_type: str,
    old_deleted: bool,
) -> None:
    old_account_id = test_user.account_id
    if not same_account:
        old_account_id = crud_account.create(
            db_session, obj_in={"organization_name": f"Other {uuid4().hex}"}
        ).id
    old = models.Tracker(
        name="Original",
        is_deleted=old_deleted,
        tracker_type=tracker_type,
        account_id=old_account_id,
        url="https://tracker.example.com",
    )
    new = models.Tracker(
        name="Reconnect",
        tracker_type=tracker_type,
        account_id=test_user.account_id,
        url="https://tracker.example.com",
    )
    db_session.add_all([old, new])
    db_session.flush()
    recent = datetime.now(timezone.utc)
    original = models.Organization(
        name="Workspace",
        identifier="shared-workspace",
        tracker_id=old.id,
        last_polling_update=recent,
    )
    db_session.add_all(
        [
            original,
            models.TrackerScopeRule(
                tracker_id=new.id,
                scope_type="ORGANIZATION",
                rule_type="INCLUDE",
                identifier="shared-workspace",
            ),
        ]
    )
    db_session.flush()
    client = TrackerClient(new, initialize_client=False)
    client.client = MagicMock()
    client.client.get_organizations = AsyncMock(
        return_value=[{"id": "shared-workspace", "name": "Workspace"}]
    )
    client.client.transform_organization.return_value = {
        "identifier": "shared-workspace",
        "name": "Workspace",
    }
    found = await client.scan_organizations(db_session)
    assert len(found) == 1
    assert found[0].id != original.id
    assert found[0].tracker_id == new.id
    assert found[0].last_polling_update is None
    db_session.refresh(original)
    assert original.tracker_id == old.id
    assert original.last_polling_update == recent


@pytest.mark.asyncio
@pytest.mark.parametrize("on_demand", [True, False])
async def test_explicit_sync_bypasses_recent_poll_but_scheduled_poll_does_not(
    on_demand: bool,
) -> None:
    from preloop.sync.scanner import core

    db = MagicMock(spec=Session)
    tracker = models.Tracker(
        id=uuid4(), name="Tracker", tracker_type="bitbucket", is_deleted=False
    )
    organization = models.Organization(
        name="Workspace",
        identifier="workspace",
        tracker_id=tracker.id,
        last_polling_update=datetime.now(timezone.utc),
    )
    scanner = MagicMock()
    scanner.scan_organizations = AsyncMock(return_value=[organization])
    process = AsyncMock(return_value={"projects": 1})
    with (
        patch.object(tasks, "get_db_session", lambda: iter([db])),
        patch.object(tasks.crud_tracker, "get", return_value=tracker),
        patch.object(tasks.crud_tracker, "validate"),
        patch.object(core, "TrackerClient", return_value=scanner),
        patch.object(core, "_process_organization", process),
    ):
        function = tasks.poll_tracker_on_demand if on_demand else tasks.poll_tracker
        stats = await function(str(tracker.id))
    assert stats is not None
    assert stats["projects"] == (1 if on_demand else 0)
    assert stats["organizations"]["skipped_polling"] == (0 if on_demand else 1)
    assert process.await_count == (1 if on_demand else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["empty", "organization", "project", "both"])
async def test_cleanup_handles_sparse_scopes_and_preserves_other_tracker_webhooks(
    db_session: Session, test_user: models.User, scope: str
) -> None:
    from preloop.models.crud import crud_webhook

    other_account = crud_account.create(
        db_session, obj_in={"organization_name": f"Other {uuid4().hex}"}
    )
    target = models.Tracker(
        name="Deleted",
        tracker_type="bitbucket",
        is_deleted=True,
        account_id=test_user.account_id,
        url="https://example.com",
    )
    other = models.Tracker(
        name="Other",
        tracker_type="bitbucket",
        is_deleted=False,
        account_id=other_account.id,
        url="https://example.com",
    )
    db_session.add_all([target, other])
    db_session.flush()
    other_org = models.Organization(
        name="Other", identifier="workspace", tracker_id=other.id
    )
    db_session.add(other_org)
    db_session.flush()
    other_project = models.Project(
        name="Other", identifier="repo", organization_id=other_org.id
    )
    db_session.add(other_project)
    db_session.flush()
    other_hook = models.Webhook(
        external_id="other-hook",
        url="https://example.com/hook",
        secret="test",
        events=[],
        project_id=other_project.id,
    )
    orphan = models.Webhook(
        external_id="unassigned",
        url="https://example.com/hook",
        secret="test",
        events=[],
    )
    db_session.add_all([other_hook, orphan])
    owned_hook = None
    mixed_hook = None
    if scope != "empty":
        org = models.Organization(
            name="Target", identifier="workspace", tracker_id=target.id
        )
        db_session.add(org)
        db_session.flush()
        project = None
        if scope in {"project", "both"}:
            project = models.Project(
                name="Target", identifier="repo", organization_id=org.id
            )
            db_session.add(project)
            db_session.flush()
        owned_hook = models.Webhook(
            external_id="owned-hook",
            url="https://example.com/hook",
            secret="test",
            events=[],
            organization_id=org.id if scope in {"organization", "both"} else None,
            project_id=project.id if project else None,
        )
        mixed_hook = models.Webhook(
            external_id="mixed-hook",
            url="https://example.com/hook",
            secret="test",
            events=[],
            organization_id=org.id,
            project_id=other_project.id,
        )
        db_session.add_all([owned_hook, mixed_hook])
    db_session.flush()
    owned_id = owned_hook.id if owned_hook else None
    preserved_ids = [other_hook.id, orphan.id] + ([mixed_hook.id] if mixed_hook else [])
    assert len(crud_webhook.get_for_tracker(db_session, tracker_id=str(target.id))) == (
        0 if scope == "empty" else 1
    )
    external = AsyncMock()
    with (
        patch.object(tasks, "get_db_session", lambda: iter([db_session])),
        patch.object(db_session, "close"),
        patch("preloop.sync.trackers.create_tracker_client", external),
    ):
        await tasks.cleanup_tracker_webhooks(str(target.id))
    external.assert_not_called()
    db_session.expire_all()
    if owned_id:
        assert db_session.get(models.Webhook, owned_id) is None
    for identifier in preserved_ids:
        assert db_session.get(models.Webhook, identifier) is not None
