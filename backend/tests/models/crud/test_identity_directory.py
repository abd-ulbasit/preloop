"""Canonical people pages preserve distinct memberships and tenant boundaries."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from preloop.api.auth.router import _build_auth_user_response, update_user_me
from preloop.models import models
from preloop.models.crud import crud_person
from preloop.models.crud.identity_directory import identity_directory
from preloop.schemas.auth import AuthUserUpdate


def member(
    db: Session, *, person: models.Person | None = None, name: str = "Jane"
) -> models.User:
    account = models.Account(organization_name=name)
    db.add(account)
    db.flush()
    suffix = uuid.uuid4().hex
    user = models.User(
        account_id=account.id,
        username=f"chosen-{suffix}" if person is None else f"generated-{suffix}",
        email=f"person-{suffix}@example.com",
        person=person,
        full_name=name,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


@pytest.mark.parametrize("verified", [False, True])
def test_one_person_pagination_search_and_filters(
    db_session: Session, verified: bool
) -> None:
    parent = member(db_session)
    child = member(db_session, person=parent.person)
    child.is_active = False
    if verified:
        parent.person.email_verified_at = datetime.now(timezone.utc)
    other = member(db_session, name="Other")
    db_session.flush()
    people, total = identity_directory.people(
        db_session, search=parent.person.username, email_verified=verified
    )
    assert total == 1 and [person.id for person in people] == [parent.person_id]
    groups = identity_directory.memberships(db_session, person_ids=[parent.person_id])
    assert {row["id"] for row in groups[parent.person_id]} == {
        str(parent.id),
        str(child.id),
    }
    assert len(groups[parent.person_id]) == 2
    assert not next(
        row for row in groups[parent.person_id] if row["id"] == str(child.id)
    )["is_active"]
    # A deterministic tie break keeps different people on separate pages.
    parent.person.created_at = other.person.created_at
    db_session.flush()
    first, total = identity_directory.people(db_session, limit=1, sort_by="created_at")
    second, next_total = identity_directory.people(
        db_session, limit=1, offset=1, sort_by="created_at"
    )
    assert total == next_total == 2
    assert first[0].id != second[0].id
    assert identity_directory.counts(db_session) == (2, 2)


def test_account_search_uses_identity_without_sibling_rows(db_session: Session) -> None:
    parent = member(db_session)
    child = member(db_session, person=parent.person)
    found, total = identity_directory.account_members(
        db_session, account_id=child.account_id, search=parent.person.username
    )
    assert total == 1 and [row.id for row in found] == [child.id]
    assert child.username != parent.person.username
    profiles, _ = identity_directory.projections_for_memberships(
        db_session, membership_ids=[row.id for row in found]
    )
    assert set(profiles) == {child.id}
    assert profiles[child.id].person_id == parent.person_id
    assert profiles[child.id].username == parent.person.username
    # The display projection never changes the original membership actor.
    assert found[0] is child and child.username != profiles[child.id].username
    assert (
        identity_directory.account_members(
            db_session, account_id=child.account_id, search=child.username
        )[1]
        == 0
    )
    child.is_active = False
    db_session.flush()
    assert (
        identity_directory.account_members(
            db_session, account_id=child.account_id, active_only=True
        )[1]
        == 0
    )


def test_activity_aggregates_original_membership_account_attribution(
    db_session: Session,
) -> None:
    parent = member(db_session)
    child = member(db_session, person=parent.person)
    unrelated = member(db_session)
    for actor in (parent, child, unrelated):
        db_session.add(
            models.Event(
                user_id=actor.id, account_id=actor.account_id, event_type="user_login"
            )
        )
    db_session.flush()
    events, total = identity_directory.activity(db_session, person_id=parent.person_id)
    assert total == 2
    assert {(event.user_id, event.account_id) for event in events} == {
        (parent.id, parent.account_id),
        (child.id, child.account_id),
    }
    people, _ = identity_directory.people(db_session, sort_by="login_count")
    assert people[0].id == parent.person_id


def test_auth_profile_updates_shared_person_and_preserves_membership_id(
    db_session: Session,
) -> None:
    parent = member(db_session)
    child = member(db_session, person=parent.person)
    response = update_user_me(
        db=db_session,
        current_user=child,
        user_update=AuthUserUpdate(
            full_name="Jane Updated", username="updated-" + uuid.uuid4().hex
        ),
    )
    assert response.id == child.id and response.person_id == parent.person_id
    assert response.account_id == child.account_id
    parent_profile = _build_auth_user_response(parent, db_session)
    assert parent_profile.username == response.username
    assert parent_profile.full_name == response.full_name == "Jane Updated"
    assert parent_profile.id == parent.id


def test_email_edit_clears_verification_and_revokes_all_membership_sessions(
    db_session: Session,
) -> None:
    parent = member(db_session)
    child = member(db_session, person=parent.person)
    parent.person.email_verified_at = datetime.now(timezone.utc)
    db_session.flush()
    original = parent.person.auth_generation
    response = update_user_me(
        db=db_session,
        current_user=child,
        user_update=AuthUserUpdate(email="updated@example.com"),
    )
    assert response.email == "updated@example.com" and not response.email_verified
    assert parent.person.auth_generation == original + 1
    assert (
        crud_person.profile_for_membership(db_session, membership_id=parent.id).email
        == response.email
    )


def test_quarantined_presentation_does_not_weaken_authentication(
    db_session: Session,
) -> None:
    from preloop.models.crud.person import IdentityUnavailableError

    row = member(db_session)
    row.person.identity_status = "quarantined"
    row.person.username = None
    row.person.email = None
    db_session.flush()
    profile = identity_directory.profile_for_membership(
        db_session, membership_id=row.id
    )
    assert profile.identity_status == "quarantined"
    assert profile.username is None and profile.email is None
    with pytest.raises(IdentityUnavailableError):
        crud_person.profile_for_membership(db_session, membership_id=row.id)


def test_expanded_memberships_show_direct_and_team_roles(db_session: Session) -> None:
    row = member(db_session)
    direct = models.Role(name="Example direct", account_id=row.account_id)
    inherited = models.Role(name="Example team", account_id=row.account_id)
    team = models.Team(name="Example team", account_id=row.account_id)
    db_session.add_all([direct, inherited, team])
    db_session.flush()
    db_session.add_all(
        [
            models.UserRole(user_id=row.id, role_id=direct.id),
            models.TeamMembership(user_id=row.id, team_id=team.id),
            models.TeamRole(team_id=team.id, role_id=inherited.id),
        ]
    )
    db_session.flush()
    groups = identity_directory.memberships(db_session, person_ids=[row.person_id])
    roles = groups[row.person_id][0]["roles"]
    assert {(role["name"], role["access"]) for role in roles} == {
        ("Example direct", "direct"),
        ("Example team", "team"),
    }
    direct_roles, team_roles = identity_directory.role_context(
        db_session, membership_id=row.id
    )
    assert direct_roles[0]["name"] == "Example direct"
    assert team_roles[0]["team_name"] == "Example team"


def test_member_page_projection_query_count_is_bounded(db_session: Session) -> None:
    from typing import Any
    from sqlalchemy import event

    rows = [member(db_session) for _ in range(40)]
    statements: list[str] = []

    def observe(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", observe)
    try:
        profiles, roles = identity_directory.projections_for_memberships(
            db_session, membership_ids=[row.id for row in rows]
        )
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe)
    assert len(profiles) == len(roles) == 40
    assert len(statements) == 3
    assert all(profile.username.startswith("chosen-") for profile in profiles.values())


def test_role_context_deduplicates_roles_inherited_from_multiple_teams(
    db_session: Session,
) -> None:
    row = member(db_session)
    role = models.Role(name="Shared role", account_id=row.account_id)
    first = models.Team(name="First team", account_id=row.account_id)
    second = models.Team(name="Second team", account_id=row.account_id)
    db_session.add_all([role, first, second])
    db_session.flush()
    for team in (first, second):
        db_session.add(models.TeamRole(team_id=team.id, role_id=role.id))
        db_session.add(models.TeamMembership(team_id=team.id, user_id=row.id))
    db_session.flush()
    direct, inherited = identity_directory.role_context(
        db_session, membership_id=row.id
    )
    assert not direct
    assert [item["id"] for item in inherited] == [role.id]
    assert inherited[0]["team_name"] == "First team"
