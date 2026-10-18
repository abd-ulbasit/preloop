"""Stable, tenant-visible source windows for the Fleet Map."""

from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from preloop.models import models

Source = Literal["agents", "models", "runners", "users"]
Resource = models.ManagedAgent | models.AIModel | models.FlowRunner | models.User


def list_resources(
    db: Session,
    *,
    source: Source,
    account_id: str,
    limit: int,
    include_built_in: bool = False,
) -> list[Resource]:
    """Read one source window without relationships or shared-account expansion.

    Args:
        db: Request database session.
        source: Allowlisted resource kind.
        account_id: Selected membership's owning tenant.
        limit: Maximum rows including a truncation sentinel.
        include_built_in: Include the available hosted model when enabled.

    Returns:
        Tenant-visible rows in stable ID order, without loading relationships.
        Decommissioned agents are never returned.
    """
    model = {
        "agents": models.ManagedAgent,
        "models": models.AIModel,
        "runners": models.FlowRunner,
        "users": models.User,
    }[source]
    visible = model.account_id == account_id
    if source == "models" and include_built_in:
        from preloop.services.built_in_models import BUILT_IN_MODEL_ALIAS

        built_in = (
            models.AIModel.account_id.is_(None)
            & (models.AIModel.meta_data["gateway"]["enabled"].astext == "true")
            & (
                models.AIModel.meta_data["gateway"]["model_alias"].astext
                == BUILT_IN_MODEL_ALIAS
            )
            & models.AIModel.meta_data["retired"].astext.is_distinct_from("true")
        )
        visible = or_(visible, built_in)
    if source == "agents":
        # Decommissioning is terminal; those rows are history, not fleet.
        visible = visible & (models.ManagedAgent.lifecycle_state != "decommissioned")
    return db.query(model).filter(visible).order_by(model.id).limit(limit).all()


def list_decommissioned_agents(
    db: Session, *, account_id: str, limit: int
) -> list[models.ManagedAgent]:
    """List the account's decommissioned agents that the map leaves out.

    Args:
        db: Request database session.
        account_id: Selected membership's owning tenant.
        limit: Maximum rows to read.

    Returns:
        Decommissioned managed agents in stable ID order, for counting after
        the caller's visibility filter.
    """
    return (
        db.query(models.ManagedAgent)
        .filter(
            models.ManagedAgent.account_id == account_id,
            models.ManagedAgent.lifecycle_state == "decommissioned",
        )
        .order_by(models.ManagedAgent.id)
        .limit(limit)
        .all()
    )
