"""Concurrent first scans must register hooks with one durable secret."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from preloop.models.crud import crud_account, crud_organization, crud_tracker


@pytest.mark.parametrize("initial", [None, "", "existing-test-secret"])
def test_concurrent_scans_share_durable_secret(
    db_engine: Engine, initial: str | None
) -> None:
    """Refresh stale ORM rows after an atomic first-writer-wins secret update."""
    with Session(db_engine) as setup:
        account = crud_account.create(
            setup, obj_in={"organization_name": f"Example {uuid4()}"}
        )
        account_id = account.id
        tracker = crud_tracker.create(
            setup,
            obj_in={
                "name": "Example GitLab",
                "tracker_type": "gitlab",
                "account_id": account_id,
            },
        )
        org = crud_organization.create(
            setup,
            obj_in={
                "name": "Example Group",
                "identifier": str(uuid4()),
                "tracker_id": tracker.id,
                "webhook_secret": initial,
            },
        )
        org_id = org.id

    barrier = Barrier(2)

    def initialize(organization_id: UUID) -> str:
        with Session(db_engine) as db:
            stale = crud_organization.get(db, id=organization_id)
            assert stale.webhook_secret == initial
            barrier.wait(timeout=15)
            secret = crud_organization.ensure_webhook_secret(
                db, organization_id=organization_id
            )
            assert stale.webhook_secret == secret
            return secret

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(initialize, [org_id, org_id]))
        assert values[0] == values[1]
        assert values[0] == initial if initial else len(values[0]) == 64
        with Session(db_engine) as verify:
            assert crud_organization.get(verify, id=org_id).webhook_secret == values[0]
    finally:
        with Session(db_engine) as cleanup:
            crud_account.delete(cleanup, id=account_id)
