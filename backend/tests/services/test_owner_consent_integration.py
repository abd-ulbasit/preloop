"""Real PostgreSQL lifecycle coverage for durable owner consent."""

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import owner_consent as crud
from preloop.services.owner_consent import check_owner_consent


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="Requires isolated PostgreSQL"
)
def test_owner_consent_lifecycle() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    account_id, owner_id = uuid4(), uuid4()
    with Session(engine) as db:
        account = models.Account(
            id=account_id,
            organization_name="Example Organization",
            meta_data={"approval_window_max_seconds": 60},
        )
        db.add(account)
        db.flush()
        db.add(
            models.User(
                id=owner_id,
                account_id=account_id,
                username=f"owner-{owner_id}",
                email=f"{owner_id}@example.com",
                hashed_password="unused",
                is_active=True,
            )
        )
        db.commit()
        try:
            kwargs = dict(
                account_id=account_id,
                owner_user_id=owner_id,
                action="runner_flow",
                subject_id="example-runner",
                request_key="example-intent",
                details={"runner_name": "Example Runner"},
                execution_id="example-execution",
            )
            first = check_owner_consent(db, **kwargs)
            second = check_owner_consent(db, **kwargs)
            assert first.created and not second.created
            assert first.approval_id == second.approval_id
            row = db.get(models.ApprovalRequest, first.approval_id)
            assert row.approval_workflow.approver_user_ids == [owner_id]
            assert row.approval_workflow.approvals_required == 1
            assert 0 < (row.expires_at - row.requested_at).total_seconds() <= 60
            with pytest.raises(ValueError):
                check_owner_consent(db, **{**kwargs, "subject_id": "other-runner"})
            row = db.get(models.ApprovalRequest, first.approval_id)
            row.status = "approved"
            db.commit()
            assert crud.consume(db, approval_id=first.approval_id)
            assert not crud.consume(db, approval_id=first.approval_id)
            pending = check_owner_consent(
                db, **{**kwargs, "request_key": "another-intent"}
            )
            row = db.get(models.ApprovalRequest, pending.approval_id)
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
            expired = check_owner_consent(
                db, **{**kwargs, "request_key": "another-intent"}
            )
            assert expired.status == "expired"
            assert not crud.consume(db, approval_id=pending.approval_id)
        finally:
            db.rollback()
            account = db.get(models.Account, account_id)
            db.delete(account)
            db.commit()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="Requires isolated PostgreSQL"
)
async def test_notification_completion_survives_delivery_commit() -> None:
    from contextlib import asynccontextmanager
    from unittest.mock import patch
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from preloop.services.owner_consent import notify_owner_consent

    engine = create_engine(os.environ["DATABASE_URL"])
    async_engine = create_async_engine(
        make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql+asyncpg")
    )
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    account_id, owner_id = uuid4(), uuid4()

    @asynccontextmanager
    async def async_session():
        async with factory() as db:
            yield db

    import asyncio

    entered, finish = asyncio.Event(), asyncio.Event()
    deliveries = 0

    async def delivered(service, approval, workflow):
        nonlocal deliveries
        deliveries += 1
        # send_notifications records its timeline with an internal commit.
        # The owner binding must remain usable by mark_notified afterwards.
        await service.db.commit()
        entered.set()
        await finish.wait()
        return {"mobile_push": {"success": True}}

    with Session(engine) as db:
        db.add(models.Account(id=account_id, organization_name="Example Organization"))
        db.flush()
        db.add(
            models.User(
                id=owner_id,
                account_id=account_id,
                username=f"owner-{owner_id}",
                email=f"{owner_id}@example.com",
                hashed_password="unused",
                is_active=True,
            )
        )
        db.commit()
        try:
            result = check_owner_consent(
                db,
                account_id=account_id,
                owner_user_id=owner_id,
                action="runner_flow",
                subject_id="example-runner",
                request_key="notification-intent",
                details={},
            )
            async with factory() as claim_db:
                original_claim = await crud.claim_notification(
                    claim_db, result.approval_id
                )
                assert original_claim is not None
                assert (
                    await crud.claim_notification(claim_db, result.approval_id) is None
                )
                binding = (
                    db.query(models.OwnerConsent)
                    .filter_by(approval_request_id=result.approval_id)
                    .one()
                )
                binding.notification_claimed_at = datetime.utcnow() - timedelta(
                    seconds=crud.NOTIFICATION_CLAIM_SECONDS + 1
                )
                db.commit()
                replacement = await crud.claim_notification(
                    claim_db, result.approval_id
                )
                assert replacement is not None and replacement != original_claim
                await crud.release_notification(
                    claim_db, approval_id=result.approval_id, claim=original_claim
                )
                assert (
                    await crud.claim_notification(claim_db, result.approval_id) is None
                )
                await crud.release_notification(
                    claim_db, approval_id=result.approval_id, claim=replacement
                )
            with (
                patch(
                    "preloop.services.owner_consent.get_async_db_session", async_session
                ),
                patch(
                    "preloop.services.approval_service.ApprovalService.send_notifications",
                    delivered,
                ),
                patch(
                    "preloop.services.approval_service._log_approval_lifecycle_async"
                ),
            ):
                first = asyncio.create_task(notify_owner_consent(result.approval_id))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=5)
                    await asyncio.wait_for(
                        notify_owner_consent(result.approval_id), timeout=5
                    )
                    assert deliveries == 1
                finally:
                    finish.set()
                    await first
            binding = (
                db.query(models.OwnerConsent)
                .filter_by(approval_request_id=result.approval_id)
                .one()
            )
            assert binding.notification_sent_at is not None
        finally:
            db.rollback()
            db.delete(db.get(models.Account, account_id))
            db.commit()
    await async_engine.dispose()
    engine.dispose()


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="Requires isolated PostgreSQL"
)
def test_shared_owner_consent_stays_private_and_tracks_execution_account() -> None:
    """A shared resource owner can approve a requester from a second account."""
    engine = create_engine(os.environ["DATABASE_URL"])
    resource_account, requester_account, owner, requester = (uuid4() for _ in range(4))
    execution_id, flow_id, runner_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as db:
        db.add_all(
            [
                models.Account(
                    id=resource_account, organization_name="Example Resource Account"
                ),
                models.Account(
                    id=requester_account, organization_name="Example Requester Account"
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                models.User(
                    id=owner,
                    account_id=resource_account,
                    username=f"owner-{owner}",
                    email=f"{owner}@example.com",
                    hashed_password="unused",
                    is_active=True,
                ),
                models.User(
                    id=requester,
                    account_id=requester_account,
                    username=f"requester-{requester}",
                    email=f"{requester}@example.com",
                    hashed_password="unused",
                    is_active=True,
                ),
                models.Flow(
                    id=flow_id,
                    account_id=requester_account,
                    name="Example Shared Flow",
                    prompt_template="Review the example change",
                    agent_type="custom",
                    agent_config={},
                ),
            ]
        )
        db.flush()
        db.add(models.FlowExecution(id=execution_id, flow_id=flow_id))
        db.commit()
        try:
            result = check_owner_consent(
                db,
                account_id=resource_account,
                owner_user_id=owner,
                requester_user_id=requester,
                action="runner_flow",
                subject_id=str(runner_id),
                request_key=f"{execution_id}:{runner_id}:{owner}",
                details={},
                execution_id=str(execution_id),
            )
            binding = (
                db.query(models.OwnerConsent)
                .filter_by(approval_request_id=result.approval_id)
                .one()
            )
            assert binding.account_id == resource_account
            assert crud.runner_for_execution(
                db, account_id=requester_account, execution_id=execution_id
            ) == str(runner_id)
            assert (
                crud.runner_for_execution(
                    db, account_id=resource_account, execution_id=execution_id
                )
                is None
            )
            status = crud.status_for_requester(
                db,
                approval_id=result.approval_id,
                account_id=requester_account,
                user_id=requester,
            )
            assert status["status"] == "pending"
            assert status["account_id"] == resource_account
            assert (
                crud.status_for_requester(
                    db,
                    approval_id=result.approval_id,
                    account_id=requester_account,
                    user_id=uuid4(),
                )
                is None
            )
            assert (
                crud.status_for_requester(
                    db,
                    approval_id=result.approval_id,
                    account_id=requester_account,
                    user_id=owner,
                )
                is None
            )
            assert (
                crud.status_for_requester(
                    db,
                    approval_id=result.approval_id,
                    account_id=resource_account,
                    user_id=owner,
                )
                is not None
            )
            assert (
                crud.poll_runner_execution(
                    db, account_id=resource_account, execution_id=execution_id
                )
                is None
            )
            approval = db.get(models.ApprovalRequest, result.approval_id)
            approval.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
            assert (
                crud.poll_runner_execution(
                    db, account_id=requester_account, execution_id=execution_id
                )
                == "expired"
            )
            expiry = (
                db.query(models.ApprovalEvent)
                .filter_by(approval_request_id=result.approval_id, event_type="expired")
                .one()
            )
            assert expiry.account_id == resource_account
        finally:
            db.rollback()
            db.delete(db.get(models.FlowExecution, execution_id))
            db.flush()
            db.delete(db.get(models.Flow, flow_id))
            db.flush()
            for account_id in [resource_account, requester_account]:
                db.delete(db.get(models.Account, account_id))
            db.commit()
    engine.dispose()
