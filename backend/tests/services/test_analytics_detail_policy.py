"""Deployment analytics policy must never gate forwarded traffic."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from preloop.services.analytics_detail_policy import should_degrade_gateway_analytics


@pytest.mark.parametrize("decision", [False, True, None])
def test_policy_decision_or_oss_default(decision: bool | None) -> None:
    policy = None if decision is None else MagicMock(return_value=decision)
    with patch("preloop.plugins.get_plugin_manager") as manager:
        manager.return_value.get_service.return_value = policy
        assert should_degrade_gateway_analytics("account") is bool(decision)
    if policy is not None:
        policy.assert_called_once_with(account_id="account")


@pytest.mark.parametrize(
    "error", [RuntimeError("unavailable"), SQLAlchemyError("query failed")]
)
def test_policy_error_cannot_poison_a_caller_transaction(error: Exception) -> None:
    with patch("preloop.plugins.get_plugin_manager") as manager:
        manager.return_value.get_service.return_value = MagicMock(side_effect=error)
        assert should_degrade_gateway_analytics("account") is False


def test_over_quota_optimization_keeps_deterministic_results() -> None:
    from types import SimpleNamespace
    from contextlib import ExitStack
    from preloop.models import models
    from preloop.schemas.gateway_usage import RuntimeSessionOptimizationRequest
    from preloop.services.session_optimization import SessionOptimizationService

    service = SessionOptimizationService(MagicMock())
    account = models.Account(id="00000000-0000-0000-0000-000000000001")
    prefix = "preloop.services.session_optimization."
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                prefix + "crud_runtime_session.get_account_session_summary",
                return_value={},
            )
        )
        stack.enter_context(patch(prefix + "require_session_history"))
        stack.enter_context(
            patch(
                prefix + "RuntimeSessionExplorerService._summary_row_to_schema",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            patch(prefix + "should_degrade_gateway_analytics", return_value=True)
        )
        stack.enter_context(
            patch(prefix + "load_session_gateway_events", return_value=[])
        )
        stack.enter_context(
            patch(prefix + "build_profile_from_events", return_value=None)
        )
        stack.enter_context(
            patch.object(
                service, "_resolve_optimization_model", return_value=MagicMock()
            )
        )
        stack.enter_context(
            patch.object(service, "_load_cached_response", return_value=None)
        )
        stack.enter_context(
            patch.object(service, "_local_optimization_suggestions", return_value=[])
        )
        stack.enter_context(patch.object(service, "_finalize_response"))
        stack.enter_context(patch.object(service, "_store_cached_response"))
        call_model = stack.enter_context(
            patch.object(service, "_call_optimization_model")
        )
        result = service.get_account_session_optimization_suggestions(
            account=account,
            runtime_session_id="session",
            request=RuntimeSessionOptimizationRequest(regenerate=True),
            current_user=MagicMock(),
        )
    assert result.generated_by == "local"
    assert result.llm_skipped_reason == "monthly_analysis_quota_reached"
    call_model.assert_not_called()
