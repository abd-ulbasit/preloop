"""Remote hooks must never receive an uncommitted organization secret."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_account, crud_organization, crud_tracker
from preloop.sync.scanner.core import TrackerClient, _process_organization


@pytest.mark.asyncio
@pytest.mark.parametrize("community_edition", [False, True])
async def test_secret_is_durable_before_remote_registration(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch, community_edition: bool
) -> None:
    """Observe the committed secret from a separate session during registration."""
    monkeypatch.setenv("PRELOOP_URL", "https://preloop.example.com")
    with Session(db_engine) as db:
        account = crud_account.create(
            db, obj_in={"organization_name": f"Example {uuid4()}"}
        )
        account_id = account.id
        try:
            tracker = crud_tracker.create(
                db,
                obj_in={
                    "name": "Example GitLab",
                    "tracker_type": "gitlab",
                    "account_id": account_id,
                    "meta_data": {"gitlab_ce": community_edition},
                },
            )
            org = crud_organization.create(
                db,
                obj_in={
                    "name": "Example Group",
                    "identifier": str(uuid4()),
                    "tracker_id": tracker.id,
                },
            )
            assert not org.webhook_secret
            client = AsyncMock(spec=TrackerClient)
            client.tracker = tracker
            client.tracker_type = "gitlab"
            client.scan_projects.return_value = [MagicMock(spec=models.Project)]
            client.scan_issues.return_value = ([], 0)
            client.client = AsyncMock()
            client.client.is_webhook_registered_for_organization.return_value = False
            client.client.is_webhook_registered_for_project.return_value = False

            async def register(**kwargs: object) -> bool:
                with Session(db_engine) as observer:
                    durable = crud_organization.get(observer, id=org.id)
                    assert durable.webhook_secret
                    assert durable.webhook_secret == kwargs["secret"]
                return True

            client.client.register_group_webhook.side_effect = register
            client.client.register_project_webhook.side_effect = register
            stats = await _process_organization(db, client, org, None, False)
            assert stats["errors"] == 0
            registration = (
                client.client.register_project_webhook
                if community_edition
                else client.client.register_group_webhook
            )
            registration.assert_awaited_once()
        finally:
            crud_account.delete(db, id=account_id)
