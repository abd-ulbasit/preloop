"""CRUD operations for Webhook model."""

from typing import List, Optional
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Query, Session
from sqlalchemy.sql.elements import ColumnElement

from preloop.models import models

from ..models.webhook import Webhook
from ..models.project import Project
from ..models.organization import Organization
from ..models.tracker import Tracker
from .base import CRUDBase


def _usable_jira_grant(now: datetime) -> ColumnElement[bool]:
    """Require an active grant with live access or refresh credentials."""
    return and_(
        models.OAuthToken.status == "active",
        or_(
            models.OAuthToken.expires_at.is_(None),
            models.OAuthToken.expires_at > now,
            and_(
                models.OAuthToken.refresh_token_encrypted.isnot(None),
                or_(
                    models.OAuthToken.refresh_token_expires_at.is_(None),
                    models.OAuthToken.refresh_token_expires_at > now,
                ),
            ),
        ),
    )


class CRUDWebhook(CRUDBase[Webhook]):
    """CRUD operations for Webhook model."""

    def _for_tracker(self, db: Session, *, tracker_id: str) -> Query:
        """Scope webhook associations to one tracker, including deleted trackers."""
        organization_ids = select(models.Organization.id).where(
            models.Organization.tracker_id == tracker_id
        )
        project_ids = select(models.Project.id).where(
            models.Project.organization_id.in_(organization_ids)
        )
        return db.query(models.Webhook).filter(
            or_(
                models.Webhook.organization_id.in_(organization_ids),
                models.Webhook.project_id.in_(project_ids),
            ),
            # A malformed mixed association must never let cleanup cross
            # tracker ownership through its other, matching association.
            or_(
                models.Webhook.organization_id.is_(None),
                models.Webhook.organization_id.in_(organization_ids),
            ),
            or_(
                models.Webhook.project_id.is_(None),
                models.Webhook.project_id.in_(project_ids),
            ),
        )

    def get_for_tracker(self, db: Session, *, tracker_id: str) -> List[models.Webhook]:
        """Return webhooks wholly owned by a tracker; empty scopes return none."""
        return self._for_tracker(db, tracker_id=tracker_id).all()

    def remove_for_tracker(self, db: Session, *, id: str, tracker_id: str) -> int:
        """Remove one webhook only while it still belongs to the target scope."""
        return (
            self._for_tracker(db, tracker_id=tracker_id)
            .filter(models.Webhook.id == id)
            .delete(synchronize_session=False)
        )

    def jira_callback_context(
        self, db: Session, *, tracker_id: str
    ) -> Optional[tuple[str, str]]:
        """Read callback namespace metadata without exposing grant secrets."""
        row = (
            db.query(models.Tracker, models.OAuthToken.provider_subject)
            .join(
                models.OAuthToken,
                and_(
                    models.OAuthToken.tracker_id == models.Tracker.id,
                    models.OAuthToken.account_id == models.Tracker.account_id,
                ),
            )
            .filter(
                models.Tracker.id == tracker_id,
                models.Tracker.tracker_type == "jira",
                models.Tracker.auth_type == "managed_oauth",
                models.OAuthToken.provider == "jira",
                models.OAuthToken.auth_mode == "managed_oauth",
                _usable_jira_grant(datetime.now(timezone.utc)),
            )
            .first()
        )
        if row is None:
            return None
        cloud_id = (row[0].connection_details or {}).get("cloud_id")
        return (str(cloud_id), str(row[1])) if cloud_id and row[1] else None

    def jira_callback_namespace(
        self,
        db: Session,
        *,
        callback_url: str,
        matched_ids: Optional[set[str]] = None,
    ) -> Optional[tuple[str, str]]:
        """Read registration proof retained independently of live grant links."""
        query = (
            db.query(models.Webhook.jira_cloud_id, models.Webhook.jira_provider_subject)
            .join(models.Project, models.Webhook.project_id == models.Project.id)
            .join(
                models.Organization,
                models.Project.organization_id == models.Organization.id,
            )
            .join(models.Tracker, models.Organization.tracker_id == models.Tracker.id)
            .filter(
                models.Webhook.url == callback_url,
                models.Webhook.organization_id.is_(None),
                models.Webhook.jira_cloud_id.isnot(None),
                models.Webhook.jira_provider_subject.isnot(None),
                models.Tracker.tracker_type == "jira",
                models.Tracker.auth_type == "managed_oauth",
            )
        )
        if matched_ids is not None:
            query = query.filter(models.Webhook.external_id.in_(matched_ids))
        contexts = {
            (str(cloud), str(subject))
            for cloud, subject in query.all()
            if cloud and subject
        }
        return next(iter(contexts)) if len(contexts) == 1 else None

    def set_jira_callback_namespace(
        self,
        db: Session,
        *,
        tracker_id: str,
        external_id: str,
        callback_url: str,
        cloud_id: str,
        provider_subject: str,
    ) -> None:
        """Backfill verified proof only on the registering tracker's own rows."""
        self._for_tracker(db, tracker_id=tracker_id).filter(
            models.Webhook.external_id == external_id,
            models.Webhook.url == callback_url,
            models.Webhook.organization_id.is_(None),
        ).update(
            {"jira_cloud_id": cloud_id, "jira_provider_subject": provider_subject},
            synchronize_session="fetch",
        )

    def get_jira_callback_projects(
        self,
        db: Session,
        *,
        callback_url: str,
        cloud_id: str,
        provider_subject: str,
        matched_ids: Optional[set[str]] = None,
    ) -> List[models.Project]:
        """Resolve only persisted callback owners with usable managed grants.

        Provider signatures alone never grant site-wide delivery access. Each
        returned project owns a registered id at this exact callback, under the
        same Jira cloud and OAuth user. Tenant joins remain explicit.
        """
        now = datetime.now(timezone.utc)
        query = (
            db.query(models.Project)
            .join(models.Webhook, models.Webhook.project_id == models.Project.id)
            .join(
                models.Organization,
                models.Project.organization_id == models.Organization.id,
            )
            .join(models.Tracker, models.Organization.tracker_id == models.Tracker.id)
            .join(
                models.OAuthToken,
                and_(
                    models.OAuthToken.tracker_id == models.Tracker.id,
                    models.OAuthToken.account_id == models.Tracker.account_id,
                ),
            )
            .join(
                models.OAuthProviderConfiguration,
                and_(
                    models.OAuthToken.configuration_id
                    == models.OAuthProviderConfiguration.id,
                    models.OAuthToken.account_id
                    == models.OAuthProviderConfiguration.account_id,
                ),
            )
            .filter(
                models.Webhook.url == callback_url,
                models.Webhook.organization_id.is_(None),
                or_(
                    models.Webhook.jira_cloud_id.is_(None),
                    models.Webhook.jira_cloud_id == cloud_id,
                ),
                or_(
                    models.Webhook.jira_provider_subject.is_(None),
                    models.Webhook.jira_provider_subject == provider_subject,
                ),
                models.Project.is_active.is_(True),
                models.Organization.is_active.is_(True),
                models.Tracker.tracker_type == "jira",
                models.Tracker.auth_type == "managed_oauth",
                models.Tracker.is_deleted.is_(False),
                models.Tracker.is_active.is_(True),
                models.OAuthToken.provider == "jira",
                models.OAuthToken.auth_mode == "managed_oauth",
                models.OAuthToken.provider_subject == provider_subject,
                models.OAuthProviderConfiguration.enabled.is_(True),
                models.OAuthProviderConfiguration.provider == "jira",
                models.OAuthToken.canonical_instance
                == models.OAuthProviderConfiguration.canonical_instance,
                models.OAuthToken.configuration_version
                == models.OAuthProviderConfiguration.version,
                _usable_jira_grant(now),
            )
        )
        if matched_ids is not None:
            query = query.filter(models.Webhook.external_id.in_(matched_ids))
        return [
            p
            for p in query.all()
            if str(
                (p.organization.tracker.connection_details or {}).get("cloud_id") or ""
            )
            == cloud_id
        ]

    def get_by_project_id(
        self, db: Session, *, project_id: str, account_id: Optional[str] = None
    ) -> Optional[Webhook]:
        """
        Get a webhook by project ID.
        """
        query = db.query(Webhook).filter(Webhook.project_id == project_id)
        if account_id:
            query = (
                query.join(Project)
                .join(Organization)
                .join(Tracker)
                .filter(Tracker.account_id == account_id)
            )
        return query.first()

    def get_all_by_project(
        self,
        db: Session,
        *,
        project_id: str,
        skip: int = 0,
        limit: int = 100,
        account_id: Optional[str] = None,
    ) -> List[Webhook]:
        """Get all webhooks for a project."""
        query = db.query(Webhook).join(Project).filter(Webhook.project_id == project_id)
        if account_id:
            query = query.join(Tracker).filter(Tracker.account_id == account_id)
        return query.offset(skip).limit(limit).all()

    def get_all_by_organization(
        self,
        db: Session,
        *,
        organization_id: str,
        skip: int = 0,
        limit: int = 100,
        account_id: Optional[str] = None,
    ) -> List[Webhook]:
        """Get all webhooks for an organization."""
        query = (
            db.query(Webhook)
            .join(Organization)
            .filter(Organization.id == organization_id)
        )
        if account_id:
            query = query.join(Tracker).filter(Tracker.account_id == account_id)
        return query.offset(skip).limit(limit).all()

    def get_by_external_id(
        self,
        db: Session,
        *,
        external_id: str,
        tracker_id: str,
        account_id: Optional[str] = None,
    ) -> Optional[Webhook]:
        """
        Get a webhook by external ID and tracker ID.

        Args:
            db: Database session
            external_id: The external ID from the tracker (e.g., GitHub webhook ID)
            tracker_id: The tracker ID to scope the lookup
            account_id: Optional account ID to filter by

        Returns:
            The webhook if found, None otherwise
        """
        # Query webhooks by external_id
        query = db.query(Webhook).filter(Webhook.external_id == external_id)

        # Join through project to get to tracker, or through organization to get to tracker
        # We need to check both paths since webhooks can belong to either project or organization
        query = (
            query.outerjoin(Project, Webhook.project_id == Project.id)
            .outerjoin(
                Organization,
                (Webhook.organization_id == Organization.id)
                | (Project.organization_id == Organization.id),
            )
            .filter(Organization.tracker_id == tracker_id)
        )

        if account_id:
            query = query.join(Tracker).filter(Tracker.account_id == account_id)

        return query.first()

    def remove(self, db: Session, *, id: str) -> Optional[Webhook]:
        """Remove a webhook by ID."""
        return db.query(Webhook).filter(Webhook.id == id).delete()
