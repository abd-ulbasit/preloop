"""PostgreSQL regressions for atomic tenant rosters and collection history."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from preloop.models import models
from preloop.models.crud import (
    crud_account,
    crud_discovery_observation,
    crud_discovered_agent_candidate,
    crud_expected_device,
)
from preloop.models.crud.discovery_observation import ObservationConflictError
from sqlalchemy.orm import Session
import pytest

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def device(ref: str) -> dict:
    return {
        "device_ref_hash": ref * 64,
        "platform": "linux",
        "attributes": {"groups": []},
    }


def observation(source_id: str | None = None, **changes: object) -> dict:
    return {
        "schema_version": 1,
        "observation_id": source_id or str(uuid4()),
        "started_at": NOW.isoformat(),
        "ended_at": NOW.isoformat(),
        "scan_scope": ["user_config"],
        "completeness": "complete",
        "applications": [],
        **changes,
    }


def test_atomic_replacement_preserves_report_history_and_tenant_isolation(
    db_session: Session,
    test_user: models.User,
) -> None:
    tenant = test_user.account_id
    other = crud_account.create(
        db_session, obj_in={"organization_name": "Synthetic tenant"}
    )
    crud_expected_device.replace(
        db_session, account_id=tenant, devices=[device("a"), device("b")]
    )
    crud_expected_device.replace(db_session, account_id=other.id, devices=[device("a")])
    obs = observation()
    args = {
        "account_id": tenant,
        "workstation_fingerprint": "a" * 64,
        "source_ref": uuid4(),
        "now": NOW,
    }
    crud_discovery_observation.record(db_session, **args, evidence=obs)
    rows = crud_expected_device.replace(
        db_session, account_id=tenant, devices=[device("a")]
    )
    assert len(rows) == 1 and rows[0].last_reported_at == NOW
    assert (
        crud_expected_device.list_for_account(db_session, account_id=other.id)[
            0
        ].last_reported_at
        is None
    )
    with pytest.raises(ObservationConflictError):
        crud_discovery_observation.record(
            db_session,
            **{**args, "now": NOW + timedelta(hours=1)},
            evidence={**obs, "completeness": "partial"},
        )
    assert (
        crud_expected_device.list_for_account(db_session, account_id=tenant)[
            0
        ].last_reported_at
        == NOW
    )
    crud_discovery_observation.purge_expired(
        db_session, account_id=tenant, now=NOW + timedelta(days=91)
    )
    assert (
        crud_expected_device.list_for_account(db_session, account_id=tenant)[
            0
        ].last_reported_at
        == NOW
    )
    crud_expected_device.replace(db_session, account_id=tenant, devices=[])
    assert crud_expected_device.list_for_account(db_session, account_id=tenant) == []
    assert (
        len(crud_expected_device.list_for_account(db_session, account_id=other.id)) == 1
    )


def test_latest_per_source_uses_collection_window_and_visible_cap(
    db_session: Session,
    test_user: models.User,
) -> None:
    tenant = test_user.account_id
    source = uuid4()
    for obs, received in [
        (observation(), NOW),
        (
            observation(
                ended_at=(NOW - timedelta(hours=1)).isoformat(),
                started_at=(NOW - timedelta(hours=1)).isoformat(),
            ),
            NOW + timedelta(hours=1),
        ),
    ]:
        crud_discovery_observation.record(
            db_session,
            account_id=tenant,
            workstation_fingerprint="a" * 64,
            source_ref=source,
            evidence=obs,
            now=received,
        )
    crud_discovery_observation.record(
        db_session,
        account_id=tenant,
        workstation_fingerprint="b" * 64,
        source_ref=uuid4(),
        evidence=observation(),
        now=NOW,
    )
    rows, total = crud_expected_device.latest_observations(
        db_session, account_id=tenant, limit=1, now=NOW + timedelta(hours=1)
    )
    assert total == 2 and len(rows) == 1 and rows[0].ended_at == NOW
    assert (
        crud_expected_device.latest_observations(
            db_session, account_id=uuid4(), now=NOW
        )[1]
        == 0
    )
    assert (
        crud_expected_device.latest_observations(
            db_session, account_id=tenant, now=NOW + timedelta(days=91)
        )[1]
        == 0
    )


def test_duplicate_roster_rejected_before_mutation(
    db_session: Session, test_user: models.User
) -> None:
    crud_expected_device.replace(
        db_session, account_id=test_user.account_id, devices=[device("a")]
    )
    with pytest.raises(ValueError, match="Duplicate"):
        crud_expected_device.replace(
            db_session,
            account_id=test_user.account_id,
            devices=[device("b"), device("b")],
        )
    assert (
        crud_expected_device.list_for_account(
            db_session, account_id=test_user.account_id
        )[0].device_ref_hash
        == "a" * 64
    )


def test_empty_legacy_report_records_history_without_claiming_evidence(
    db_session: Session,
    test_user: models.User,
) -> None:
    tenant = test_user.account_id
    crud_expected_device.replace(db_session, account_id=tenant, devices=[device("a")])
    crud_discovered_agent_candidate.record_report(
        db_session,
        account_id=tenant,
        workstation_fingerprint="a" * 64,
        os_family="linux",
        cli_version=None,
        candidates=[],
        now=NOW.replace(tzinfo=None),
    )
    assert (
        crud_expected_device.list_for_account(db_session, account_id=tenant)[
            0
        ].last_reported_at
        == NOW
    )
    assert (
        crud_expected_device.latest_observations(
            db_session, account_id=tenant, now=NOW
        )[1]
        == 0
    )


def test_import_after_report_seeds_history_before_retention(
    db_session: Session,
    test_user: models.User,
) -> None:
    tenant = test_user.account_id
    crud_discovery_observation.record(
        db_session,
        account_id=tenant,
        workstation_fingerprint="a" * 64,
        source_ref=uuid4(),
        evidence=observation(),
        now=NOW,
    )
    rows = crud_expected_device.replace(
        db_session, account_id=tenant, devices=[device("a")]
    )
    assert rows[0].last_reported_at == NOW
    crud_discovery_observation.purge_expired(
        db_session, account_id=tenant, now=NOW + timedelta(days=91)
    )
    assert (
        crud_expected_device.list_for_account(db_session, account_id=tenant)[
            0
        ].last_reported_at
        == NOW
    )
