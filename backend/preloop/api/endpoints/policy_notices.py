"""Policy notice summary for the Attention page (#959) and #1710.

Model I/O matches record a hit per match. Notify hits feed the Attention
card. Deny and require_approval hits are included when the caller groups
by developer or action. Dismissal goes through the existing Attention
dismissal API; the card's fingerprint includes the latest hit id, so a new
hit brings a dismissed card back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from preloop.api.auth.jwt import get_current_active_user
from preloop.api.common import get_account_for_user
from preloop.models.db.session import get_db_session
from preloop.models.models.account import Account
from preloop.models.models.policy_notice_hit import POLICY_NOTICE_SUMMARY_LIMIT
from preloop.models.models.user import User
from preloop.services.policy_notices import summarize_policy_notice_groups
from preloop.utils.permissions import require_permission

router = APIRouter()


class PolicyNoticeRuleSummaryResponse(BaseModel):
    """Hits of one rule inside the window."""

    rule_id: str = Field(..., description="Model I/O rule id")
    rule_description: Optional[str] = Field(None, description="Rule description")
    target: str = Field(..., description="model.request or model.response")
    action: str = Field(
        ...,
        description=(
            "Newest hit's stored action: notify, deny, or require_approval. "
            "redact is reserved and is not written."
        ),
    )
    count: int = Field(..., description="Hits in the window")
    last_hit_id: UUID = Field(..., description="Newest hit id (card fingerprint)")
    last_hit_at: datetime = Field(..., description="Newest hit time (UTC)")
    last_excerpt: Optional[str] = Field(
        None, description="Newest redacted excerpt (at most 280 characters)"
    )
    last_user_id: Optional[UUID] = Field(None, description="Newest hit's user")
    last_username: Optional[str] = Field(None, description="Newest hit's username")


class PolicyNoticeUserSummaryResponse(BaseModel):
    """Hits of one developer inside the window."""

    user_id: Optional[UUID] = Field(None, description="User id, when the hit has one")
    username: Optional[str] = Field(None, description="Username in this account")
    count: int = Field(..., description="Hits in the window")


class PolicyNoticeActionSummaryResponse(BaseModel):
    """Hits of one action inside the window."""

    action: str = Field(
        ...,
        description=(
            "Stored action that occurred: notify, deny, or require_approval. "
            "redact is reserved on the column and is not written."
        ),
    )
    count: int = Field(..., description="Hits in the window")


class PolicyNoticeBreakdownResponse(BaseModel):
    """Hits sharing one model alias or agent reference."""

    key: Optional[str] = Field(None, description="Model alias or agent reference")
    count: int = Field(..., description="Hits in the window")


class PolicyNoticeSummaryResponse(BaseModel):
    """Policy matches for one window, with model and agent breakdowns."""

    days: int = Field(..., description="Window length in days")
    group_by: Literal["rule", "user", "action"] = Field(
        ..., description="Grouping requested by the caller"
    )
    rules: List[PolicyNoticeRuleSummaryResponse]
    users: List[PolicyNoticeUserSummaryResponse]
    actions: List[PolicyNoticeActionSummaryResponse]
    models: List[PolicyNoticeBreakdownResponse]
    agents: List[PolicyNoticeBreakdownResponse]


@router.get(
    "/policies/notices/summary",
    response_model=PolicyNoticeSummaryResponse,
    summary="Summarize policy matches",
)
@require_permission("view_policies")
def get_policy_notice_summary(
    days: int = Query(7, ge=1, le=90, description="Window length in days"),
    group_by: Literal["rule", "user", "action"] = Query(
        "rule",
        description=(
            "rule keeps the Attention list to notify hits. "
            "user and action include deny and require_approval hits. "
            f"Rule and user lists are each capped at {POLICY_NOTICE_SUMMARY_LIMIT}. "
            "Within that cap, per-user counts sum to the rule totals."
        ),
    ),
    account: Account = Depends(get_account_for_user),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db_session),
) -> PolicyNoticeSummaryResponse:
    """Return policy matches from the last ``days`` days.

    The default ``group_by=rule`` list is notify hits, which is what the
    Attention page renders. ``group_by=user`` is the per-developer summary.
    Rule and user lists are each capped at 100. Within that cap, user
    counts add up to rule counts. Past it the two lists are truncated
    independently and the sums can diverge. Model and agent breakdowns
    count every stored action either way, and each of those lists uses
    the same cap.
    """
    summary = summarize_policy_notice_groups(
        db,
        account.id,
        now=datetime.now(timezone.utc),
        window=timedelta(days=days),
        group_by=group_by,
    )
    return PolicyNoticeSummaryResponse(
        days=days,
        group_by=group_by,
        rules=[
            PolicyNoticeRuleSummaryResponse(
                rule_id=row.rule_id,
                rule_description=row.rule_description,
                target=row.target,
                action=row.action,
                count=row.count,
                last_hit_id=row.last_hit_id,
                last_hit_at=row.last_hit_at,
                last_excerpt=row.last_excerpt,
                last_user_id=row.last_user_id,
                last_username=row.last_username,
            )
            for row in summary.rules
        ],
        users=[
            PolicyNoticeUserSummaryResponse(
                user_id=row.user_id,
                username=row.username,
                count=row.count,
            )
            for row in summary.users
        ],
        actions=[
            PolicyNoticeActionSummaryResponse(action=row.action, count=row.count)
            for row in summary.actions
        ],
        models=[
            PolicyNoticeBreakdownResponse(key=row.key, count=row.count)
            for row in summary.models
        ],
        agents=[
            PolicyNoticeBreakdownResponse(key=row.key, count=row.count)
            for row in summary.agents
        ],
    )
