"""Bounded, account-scoped Stage A candidate reads."""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from preloop.models import models


def list_session_digest_candidates(
    db: Session,
    *,
    account_id: UUID,
    after_id: UUID | None = None,
    limit: int = 200,
) -> list[UUID]:
    """Return a keyset page with real traffic beyond the last digest.

    Counts preserve equal-timestamp arrivals; the timestamp comparison also
    handles retained histories whose cumulative count has decreased. Only IDs
    cross the CRUD boundary, so unchanged sessions never load their histories.
    Keyset pagination remains stable while workers append digest segments.
    """
    runtime = models.RuntimeSession
    usage = models.ApiUsage
    digest = models.GovernanceSessionDigest
    traffic = (
        usage.account_id == account_id,
        usage.runtime_session_id == runtime.id,
        usage.action_type == "model_gateway",
        or_(
            usage.meta_data["purpose"].astext.is_(None),
            usage.meta_data["purpose"].astext == "",
        ),
    )
    count = (
        select(func.count(usage.id))
        .where(*traffic)
        .correlate(runtime)
        .scalar_subquery()
    )
    latest = (
        select(func.max(usage.timestamp))
        .where(*traffic)
        .correlate(runtime)
        .scalar_subquery()
    )
    segment = (
        select(digest.request_watermark)
        .where(digest.account_id == account_id, digest.runtime_session_id == runtime.id)
        .order_by(digest.segment_index.desc())
        .limit(1)
        .correlate(runtime)
        .scalar_subquery()
    )
    end = (
        select(digest.window_end)
        .where(digest.account_id == account_id, digest.runtime_session_id == runtime.id)
        .order_by(digest.segment_index.desc())
        .limit(1)
        .correlate(runtime)
        .scalar_subquery()
    )
    statement = select(runtime.id).where(
        runtime.account_id == account_id,
        or_(count > func.coalesce(segment, 0), latest > end),
    )
    if after_id is not None:
        statement = statement.where(runtime.id > after_id)
    return list(
        db.scalars(statement.order_by(runtime.id).limit(min(max(limit, 1), 200))).all()
    )
