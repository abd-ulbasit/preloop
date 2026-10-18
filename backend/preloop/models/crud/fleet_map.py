"""Stable, tenant-owned source windows for the Fleet Map."""

from typing import Literal

from sqlalchemy.orm import Session

from preloop.models import models

Source = Literal["agents", "models", "runners", "users"]
Resource = models.ManagedAgent | models.AIModel | models.FlowRunner | models.User


def list_resources(
    db: Session, *, source: Source, account_id: str, limit: int
) -> list[Resource]:
    """Read one source window without relationships or shared-account expansion.

    Args:
        db: Request database session.
        source: Allowlisted resource kind.
        account_id: Selected membership's owning tenant.
        limit: Maximum rows including a truncation sentinel.

    Returns:
        Tenant-owned rows in stable ID order, without loading relationships.
    """
    model = {
        "agents": models.ManagedAgent,
        "models": models.AIModel,
        "runners": models.FlowRunner,
        "users": models.User,
    }[source]
    return (
        db.query(model)
        .filter(model.account_id == account_id)
        .order_by(model.id)
        .limit(limit)
        .all()
    )
