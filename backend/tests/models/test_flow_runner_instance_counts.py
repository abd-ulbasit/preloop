"""Batched runner counts for the admin instances page.

``counts_for_instances`` replaces three queries per row on a page of up to
500 instances; it must return exactly what ``counts_for_instance`` returns
for every id, including instances without runners.
"""

import uuid
from datetime import datetime, timedelta, timezone

from preloop.models.crud import crud_account
from preloop.models.crud.flow_runner import crud_flow_runner
from preloop.models.models import FlowRunner, Instance


def _instance(db) -> Instance:
    now = datetime.now(timezone.utc)
    instance = Instance(
        instance_uuid=uuid.uuid4(),
        version="0.15.0",
        edition="oss",
        first_seen=now,
        last_seen=now,
        check_count="1",
        is_active=True,
    )
    db.add(instance)
    db.flush()
    return instance


def _runner(db, account, instance, *, status: str, heartbeat_age: timedelta | None):
    db.add(
        FlowRunner(
            account_id=account.id,
            instance_id=instance.id,
            name=f"runner-{uuid.uuid4().hex[:8]}",
            status=status,
            token_hash=uuid.uuid4().hex,
            last_heartbeat=(
                None
                if heartbeat_age is None
                else datetime.now(timezone.utc) - heartbeat_age
            ),
        )
    )
    db.flush()


def test_counts_for_instances_matches_per_instance_counts(db_session):
    account = crud_account.create(
        db_session, obj_in={"organization_name": "runner-count-org"}
    )
    busy, stale, empty = (_instance(db_session) for _ in range(3))
    _runner(db_session, account, busy, status="online", heartbeat_age=timedelta(0))
    _runner(db_session, account, busy, status="busy", heartbeat_age=timedelta(0))
    _runner(db_session, account, busy, status="offline", heartbeat_age=None)
    _runner(
        db_session, account, stale, status="online", heartbeat_age=timedelta(days=1)
    )

    ids = [busy.id, stale.id, empty.id]
    batch = crud_flow_runner.counts_for_instances(db_session, instance_ids=ids)

    for instance_id in ids:
        assert batch[instance_id] == crud_flow_runner.counts_for_instance(
            db_session, instance_id=instance_id
        )
    assert batch[busy.id]["runner_count"] == 3
    assert batch[busy.id]["online_runner_count"] == 2
    assert batch[stale.id]["online_runner_count"] == 0
    assert batch[empty.id] == {
        "runner_count": 0,
        "online_runner_count": 0,
        "last_runner_heartbeat": None,
    }


def test_counts_for_instances_empty_page(db_session):
    assert crud_flow_runner.counts_for_instances(db_session, instance_ids=[]) == {}
