"""Canonical identity projections with explicit account membership boundaries.

Platform callers must authorize before using ``people`` or ``memberships``.
Tenant callers use ``account_members`` and never receive sibling memberships.
Account capacity continues to count User rows, independently of this directory.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud.person import crud_person


@dataclass(frozen=True)
class IdentityPresentation:
    """Authorized display fields; migration conflicts never fall back to aliases."""

    person_id: UUID
    username: str | None
    email: str | None
    email_verified: bool
    full_name: str | None
    avatar_url: str | None
    avatar_source: str | None
    identity_status: str


class IdentityDirectory:
    """Queries for identity presentation; historical actor IDs stay untouched."""

    def profile_for_membership(
        self, db: Session, *, membership_id: UUID
    ) -> IdentityPresentation:
        """Display an explicit unavailable state without authorizing authentication."""
        person = crud_person.resolve_for_membership(db, membership_id=membership_id)
        return self.profile_for_person(person)

    @staticmethod
    def profile_for_person(person: models.Person) -> IdentityPresentation:
        """Authorized platform display, including retained orphan identities."""
        available = person.identity_status != "quarantined"
        return IdentityPresentation(
            person_id=person.id,
            username=person.username if available else None,
            email=person.email if available else None,
            email_verified=available and person.email_verified_at is not None,
            full_name=person.full_name if available else None,
            avatar_url=person.avatar_url if available else None,
            avatar_source=person.avatar_source if available else None,
            identity_status=person.identity_status,
        )

    def account_members(
        self,
        db: Session,
        *,
        account_id: UUID,
        search: str | None = None,
        active_only: bool = False,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[models.User], int]:
        """Search canonical profiles, returning only the named account's rows."""
        query = (
            db.query(models.User)
            .join(models.Person, models.Person.id == models.User.person_id)
            .filter(models.User.account_id == account_id)
        )
        if search:
            query = query.filter(
                or_(
                    models.Person.username.ilike(f"%{search}%"),
                    models.Person.email.ilike(f"%{search}%"),
                    models.Person.full_name.ilike(f"%{search}%"),
                )
            )
        if active_only:
            query = query.filter(models.User.is_active.is_(True))
        total = query.count()
        rows = (
            query.order_by(models.Person.username, models.User.id)
            .offset(skip)
            .limit(limit)
            .all()
        )
        return rows, total

    def people(
        self,
        db: Session,
        *,
        search: str | None = None,
        is_active: bool | None = None,
        email_verified: bool | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[models.Person], int]:
        """Count/page people, using aggregate membership activity for sorting."""
        member_stats = (
            db.query(
                models.User.person_id.label("person_id"),
                func.max(models.User.last_login).label("last_login"),
                func.min(models.Account.organization_name).label("account_name"),
            )
            .join(models.Account, models.Account.id == models.User.account_id)
            .group_by(models.User.person_id)
            .subquery()
        )
        login_stats = (
            db.query(
                models.User.person_id.label("person_id"),
                func.count(models.Event.id).label("login_count"),
            )
            .join(models.Event, models.Event.user_id == models.User.id)
            .filter(models.Event.event_type == "user_login")
            .group_by(models.User.person_id)
            .subquery()
        )
        query = (
            db.query(models.Person)
            .outerjoin(member_stats, member_stats.c.person_id == models.Person.id)
            .outerjoin(login_stats, login_stats.c.person_id == models.Person.id)
        )
        if search:
            query = query.filter(
                or_(
                    models.Person.username.ilike(f"%{search}%"),
                    models.Person.email.ilike(f"%{search}%"),
                    models.Person.full_name.ilike(f"%{search}%"),
                )
            )
        if is_active is not None:
            query = query.filter(
                models.Person.identity_status == "active"
                if is_active
                else models.Person.identity_status != "active"
            )
        if email_verified is not None:
            query = query.filter(
                models.Person.email_verified_at.is_not(None)
                if email_verified
                else models.Person.email_verified_at.is_(None)
            )
        total = query.count()
        sort_column = {
            "username": models.Person.username,
            "email": models.Person.email,
            "created_at": models.Person.created_at,
            "last_login": member_stats.c.last_login,
            "account_name": member_stats.c.account_name,
            "login_count": func.coalesce(login_stats.c.login_count, 0),
        }[sort_by]
        ordering = sort_column.asc() if sort_order == "asc" else sort_column.desc()
        return query.order_by(ordering.nulls_last(), models.Person.id).offset(
            offset
        ).limit(limit).all(), total

    def memberships(
        self, db: Session, *, person_ids: list[UUID]
    ) -> dict[UUID, list[dict[str, Any]]]:
        """Batch membership context for an already authorized platform page."""
        if not person_ids:
            return {}
        rows = (
            db.query(models.User, models.Account)
            .join(models.Account, models.Account.id == models.User.account_id)
            .filter(models.User.person_id.in_(person_ids))
            .order_by(models.User.created_at, models.User.id)
            .all()
        )
        member_ids = [user.id for user, _ in rows]
        role_rows = (
            db.query(models.UserRole, models.Role)
            .join(models.Role, models.Role.id == models.UserRole.role_id)
            .filter(models.UserRole.user_id.in_(member_ids))
            .all()
        )
        roles: dict[UUID, list[dict[str, Any]]] = {}
        for assignment, role in role_rows:
            roles.setdefault(assignment.user_id, []).append(
                {
                    "id": str(role.id),
                    "name": role.name,
                    "access": "inherited"
                    if getattr(assignment, "access_grant_id", None)
                    else "direct",
                }
            )
        team_roles = (
            db.query(models.TeamMembership.user_id, models.Role.name, models.Team.name)
            .join(
                models.TeamRole,
                models.TeamRole.team_id == models.TeamMembership.team_id,
            )
            .join(models.Role, models.Role.id == models.TeamRole.role_id)
            .join(models.Team, models.Team.id == models.TeamMembership.team_id)
            .filter(models.TeamMembership.user_id.in_(member_ids))
            .all()
        )
        for member_id, role_name, team_name in team_roles:
            roles.setdefault(member_id, []).append(
                {"name": role_name, "access": "team", "team_name": team_name}
            )
        result: dict[UUID, list[dict[str, Any]]] = {}
        for member, account in rows:
            result.setdefault(member.person_id, []).append(
                {
                    "id": str(member.id),
                    "account_id": str(member.account_id),
                    "organization_name": account.organization_name,
                    "is_active": member.is_active,
                    "is_superuser": bool(member.is_superuser),
                    "membership_kind": member.membership_kind,
                    "roles": roles.get(member.id, []),
                    "last_login": member.last_login.isoformat()
                    if member.last_login
                    else None,
                    "created_at": member.created_at.isoformat()
                    if member.created_at
                    else None,
                }
            )
        return result

    def role_context(
        self, db: Session, *, membership_id: UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Roles for one membership, including team inheritance in its account."""
        direct = [
            {"id": role.id, "name": role.name, "description": role.description}
            for role in db.query(models.Role)
            .join(models.UserRole, models.UserRole.role_id == models.Role.id)
            .filter(models.UserRole.user_id == membership_id)
            .all()
        ]
        inherited = [
            {
                "id": role.id,
                "name": role.name,
                "description": role.description,
                "team_name": team.name,
            }
            for role, team in db.query(models.Role, models.Team)
            .join(models.TeamRole, models.TeamRole.role_id == models.Role.id)
            .join(models.Team, models.Team.id == models.TeamRole.team_id)
            .join(
                models.TeamMembership, models.TeamMembership.team_id == models.Team.id
            )
            .filter(models.TeamMembership.user_id == membership_id)
            .all()
        ]
        return direct, inherited

    def activity(
        self, db: Session, *, person_id: UUID, limit: int = 100, offset: int = 0
    ) -> tuple[list[models.Event], int]:
        """Platform activity preserving original membership and account IDs."""
        query = (
            db.query(models.Event)
            .join(models.User, models.User.id == models.Event.user_id)
            .filter(models.User.person_id == person_id)
        )
        return query.order_by(models.Event.timestamp.desc(), models.Event.id).offset(
            offset
        ).limit(limit).all(), query.count()

    def counts(self, db: Session) -> tuple[int, int]:
        """Platform human counts; never use for account seat billing."""
        total, active = db.query(
            func.count(models.Person.id),
            func.sum(case((models.Person.identity_status == "active", 1), else_=0)),
        ).one()
        return int(total or 0), int(active or 0)


identity_directory = IdentityDirectory()
