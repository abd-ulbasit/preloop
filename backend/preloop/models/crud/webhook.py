"""CRUD operations for Webhook model."""

from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Query, Session

from preloop.models import models

from ..models.webhook import Webhook
from ..models.project import Project
from ..models.organization import Organization
from ..models.tracker import Tracker
from .base import CRUDBase


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
