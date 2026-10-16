"""Jira scope uses stable project IDs and honors previously saved project keys."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.sync.scanner.core import TrackerClient
from preloop.sync.trackers.jira import JiraTracker


@pytest.mark.asyncio
@pytest.mark.parametrize("include_organization", [True, False])
@pytest.mark.parametrize(
    ("inclusions", "exclusions", "expected"),
    [
        (["10000"], [], ["10000"]),
        (["PROJ"], [], ["10000"]),
        ([], ["10000"], ["10001"]),
        ([], ["PROJ"], ["10001"]),
        (["10000"], ["PROJ"], []),
        (["PROJ"], ["10000"], []),
        (["UNSELECTED"], [], []),
    ],
)
async def test_jira_listing_selection_matches_scanner(
    db_session: Session,
    test_user: models.User,
    inclusions: list[str],
    exclusions: list[str],
    expected: list[str],
    include_organization: bool,
) -> None:
    tracker = models.Tracker(
        name="Jira",
        tracker_type="jira",
        account_id=test_user.account_id,
        url="https://jira.example.com",
    )
    db_session.add(tracker)
    db_session.flush()
    org = models.Organization(
        name="Jira", identifier="jira.example.com", tracker_id=tracker.id
    )
    db_session.add(org)
    # A rule owned by a different tracker/account must never grant access.
    from preloop.models.crud import crud_account

    other_account = crud_account.create(
        db_session, obj_in={"organization_name": "Other account"}
    )
    other = models.Tracker(
        name="Other Jira",
        tracker_type="jira",
        account_id=other_account.id,
        url="https://other.example.com",
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(
        models.TrackerScopeRule(
            tracker_id=other.id,
            scope_type="ORGANIZATION",
            rule_type="INCLUDE",
            identifier=org.identifier,
        )
    )
    db_session.add(
        models.TrackerScopeRule(
            tracker_id=other.id,
            scope_type="PROJECT",
            rule_type="INCLUDE",
            identifier="OTHER",
        )
    )
    if include_organization:
        db_session.add(
            models.TrackerScopeRule(
                tracker_id=tracker.id,
                scope_type="ORGANIZATION",
                rule_type="INCLUDE",
                identifier=org.identifier,
            )
        )
    for rule_type, identifiers in [("INCLUDE", inclusions), ("EXCLUDE", exclusions)]:
        for identifier in identifiers:
            db_session.add(
                models.TrackerScopeRule(
                    tracker_id=tracker.id,
                    scope_type="PROJECT",
                    rule_type=rule_type,
                    identifier=identifier,
                )
            )
    db_session.flush()
    with patch("preloop.sync.trackers.jira.JIRA"):
        adapter = JiraTracker(
            str(tracker.id),
            "test-token",
            {
                "url": tracker.url,
                "username": "test@example.com",
            },
        )
    adapter._make_request = AsyncMock(
        return_value=[
            {"id": "10000", "key": "PROJ", "name": "Selected"},
            {"id": "10001", "key": "OTHER", "name": "Other"},
        ]
    )
    scanner = TrackerClient(tracker, initialize_client=False)
    scanner.client = adapter
    found = await scanner.scan_projects(db_session, org)
    expected = expected if include_organization else []
    assert sorted(project.identifier for project in found) == expected
    assert all(project.organization_id == org.id for project in found)
    if "10000" in expected:
        assert (
            next(project for project in found if project.identifier == "10000").slug
            == "PROJ"
        )


@pytest.mark.asyncio
async def test_jira_listing_identifier_is_the_persisted_project_identity() -> None:
    with patch("preloop.sync.trackers.jira.JIRA"):
        adapter = JiraTracker(
            "tracker",
            "test-token",
            {"url": "https://jira.example.com", "username": "test@example.com"},
        )
    adapter._make_request = AsyncMock(
        return_value=[
            {"id": "10000", "key": "PROJ", "name": "Selected"},
        ]
    )
    listed = (await adapter.get_projects("jira.example.com"))[0]
    transformed = adapter.transform_project(listed, "organization")
    assert listed["identifier"] == transformed["identifier"] == "10000"
    assert listed["key"] == transformed["slug"] == "PROJ"
