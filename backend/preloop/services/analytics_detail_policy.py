"""Optional deployment policy for derived gateway analytics.

This decision applies only after the upstream response, never to governance,
budget enforcement, request forwarding or accounting. OSS keeps full detail.
"""

from __future__ import annotations

import logging
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def should_degrade_gateway_analytics(db: Session, account_id: str) -> bool:
    """Consult an optional analytics policy, failing open on policy errors."""
    try:
        from preloop.plugins import get_plugin_manager

        policy = get_plugin_manager().get_service("analytics_detail_policy")
        return policy is not None and bool(policy(db, account_id=account_id))
    except Exception:
        logger.warning(
            "Analytics detail policy failed; retaining detail", exc_info=True
        )
        # Policies own their read sessions; the caller may have pending
        # runtime-session or optimization writes that must remain intact.
        return False
