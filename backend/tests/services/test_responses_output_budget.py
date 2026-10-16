"""Bound hosted reasoning output before reserving budget and calling upstream."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from preloop.services.model_gateway_auth import ModelGatewayAuthContext
from preloop.services.openai_gateway import OpenAIGatewayService


def _service() -> OpenAIGatewayService:
    return OpenAIGatewayService(
        MagicMock(),
        ModelGatewayAuthContext(
            token="token", user=SimpleNamespace(id="user", account_id="account")
        ),
    )


def _model(**overrides: object) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            "id": "model",
            "provider_name": "qwen",
            "model_identifier": "deepseek-v4.1-flash",
            "model_parameters": None,
            "api_endpoint": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "meta_data": {"hosted": True},
            "account_id": None,
            **overrides,
        }
    )


@pytest.mark.parametrize("stream", [False, True])
def test_hosted_reasoning_default_reaches_budget_and_upstream(stream: bool) -> None:
    service = _service()
    payload = {"model": "qwen/deepseek-v4.1-flash-hosted", "input": "Review"}
    model = _model()
    response = {"choices": [{"message": {"content": "Done"}}]}
    chunks = iter(
        [{"choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]}]
    )
    with (
        patch.object(service, "_resolve_requested_model", return_value=model),
        patch.object(service, "_check_budget", return_value=None) as budget,
        patch.object(
            service, "_call_litellm", return_value=chunks if stream else response
        ) as upstream,
        patch.object(service, "_record_gateway_request"),
    ):
        if stream:
            list(service.stream_response(payload))
        else:
            service.create_response(payload)
    assert budget.call_args.args[1]["max_output_tokens"] == 8192
    assert upstream.call_args.kwargs["payload"]["max_output_tokens"] == 8192
    assert "max_output_tokens" not in payload


@pytest.mark.parametrize(
    "field", ["max_tokens", "max_completion_tokens", "max_output_tokens"]
)
def test_explicit_output_limits_survive_chat_translation(field: str) -> None:
    service = _service()
    with patch("preloop.services.openai_gateway.get_secret_service") as secret:
        secret.return_value.resolve_ai_model_credentials.return_value = SimpleNamespace(
            credential_type="api_key", value="synthetic-secret"
        )
        kwargs = service._build_completion_kwargs(
            _model(),
            messages=[{"role": "user", "content": "Review"}],
            payload={field: 256},
            stream=True,
            provider="openai",
        )
    assert kwargs["max_tokens"] == 256


@pytest.mark.parametrize(
    "field", ["max_tokens", "max_completion_tokens", "max_output_tokens"]
)
def test_default_never_expands_explicit_limit(field: str) -> None:
    payload = {field: 256, "input": "Review"}
    assert _service()._responses_output_budget(_model(), payload) is payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_name": "deepseek", "api_endpoint": "https://api.deepseek.com/v1"},
        {"model_identifier": "qwen3.5-plus"},
        {"provider_name": "openai-compatible"},
        {"provider_name": "custom"},
        {"meta_data": {}, "account_id": "byok-account"},
    ],
)
def test_default_does_not_change_other_routes(overrides: dict[str, object]) -> None:
    payload = {"input": "Review"}
    assert _service()._responses_output_budget(_model(**overrides), payload) is payload


def test_default_respects_operator_model_ceiling_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from preloop.config import settings

    monkeypatch.setattr(settings, "gateway_hosted_reasoning_max_output_tokens", 4096)
    assert (
        _service()._responses_output_budget(_model(), {})["max_output_tokens"] == 4096
    )
    assert (
        _service()._responses_output_budget(
            _model(model_parameters={"max_output_tokens": 512}), {}
        )["max_output_tokens"]
        == 512
    )


def test_reasoning_only_length_reports_exhausted_budget_without_completion() -> None:
    import json

    service = _service()
    chunks = iter(
        [
            {"choices": [{"delta": {"reasoning_content": "Synthetic reasoning"}}]},
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 31619,
                    "completion_tokens": 1024,
                    "total_tokens": 32643,
                    "completion_tokens_details": {"reasoning_tokens": 1024},
                },
            },
        ]
    )
    with (
        patch.object(service, "_resolve_requested_model", return_value=_model()),
        patch.object(service, "_check_budget", return_value=None),
        patch.object(service, "_call_litellm", return_value=chunks),
        patch.object(service, "_record_gateway_request") as record,
    ):
        events = [
            json.loads(e.split("data: ", 1)[1])
            for e in service.stream_response(
                {
                    "model": "qwen/deepseek-v4.1-flash-hosted",
                    "input": "Review",
                    "max_output_tokens": 1024,
                }
            )
            if "[DONE]" not in e
        ]
    error = next(e for e in events if e.get("type") == "error")
    assert error["code"] == "output_budget_exhausted"
    assert "exhausted the output token budget" in error["message"]
    assert not any(e.get("type") == "response.completed" for e in events)
    assert record.call_args.kwargs["status_code"] == 502


def test_busy_tariff_allowance_fits_small_flow_budget() -> None:
    from datetime import datetime, timezone
    from preloop.services.model_pricing import estimate_ai_model_usage_cost

    # Conservative: all input uncached at the documented busy-hour tariff.
    cost = estimate_ai_model_usage_cost(
        _model(),
        prompt_tokens=31619,
        completion_tokens=8192,
        total_tokens=39811,
        observed_at=datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc),
    )
    assert cost is not None
    assert cost == pytest.approx(0.019316)
    assert cost < 0.20 < 0.25


def test_native_responses_remains_unchanged() -> None:
    payload = {"input": "Review"}
    with patch(
        "preloop.services.openai_gateway.should_use_responses_passthrough",
        return_value=True,
    ):
        assert _service()._responses_output_budget(_model(), payload) is payload


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reason", ["account_budget_exceeded", "flow_budget_exceeded"])
def test_default_allowance_cannot_bypass_budget_denial(
    stream: bool, reason: str
) -> None:
    from dataclasses import fields
    from preloop.services.model_gateway_budget import BudgetCheckResult
    from preloop.services.model_gateway_errors import ModelGatewayAPIError

    service = _service()
    values = {field.name: None for field in fields(BudgetCheckResult)}
    values.update(
        account_current_spend_usd=0.19,
        flow_current_spend_usd=0.19,
        account_limit_usd=0.20,
        flow_limit_usd=0.20,
        estimated_request_cost_usd=0.019316,
        account_estimated_total_usd=0.209316,
        flow_estimated_total_usd=0.209316,
        hard_limit_exceeded=True,
        soft_limit_exceeded=False,
        enforcement_reason=reason,
        pricing_available=True,
    )
    with (
        patch.object(service, "_resolve_requested_model", return_value=_model()),
        patch.object(
            service, "_check_budget", return_value=BudgetCheckResult(**values)
        ) as budget,
        patch.object(service, "_call_litellm") as upstream,
        patch.object(service, "_record_gateway_request"),
    ):
        with pytest.raises(ModelGatewayAPIError) as denied:
            if stream:
                list(service.stream_response({"input": "Review"}))
            else:
                service.create_response({"input": "Review"})
        assert denied.value.status_code == 429
    assert budget.call_args.args[1]["max_output_tokens"] == 8192
    upstream.assert_not_called()


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"max_tokens": 8192, "max_output_tokens": 256}, 256),
        (
            {
                "max_tokens": 8192,
                "max_output_tokens": 512,
                "max_completion_tokens": 256,
            },
            256,
        ),
    ],
)
def test_alias_precedence_matches_budget_estimate(
    payload: dict[str, int], expected: int
) -> None:
    """Multiple aliases must not reserve a small cap then send a larger one."""
    with patch("preloop.services.openai_gateway.get_secret_service") as secret:
        secret.return_value.resolve_ai_model_credentials.return_value = SimpleNamespace(
            credential_type="api_key", value="synthetic-secret"
        )
        kwargs = _service()._build_completion_kwargs(
            _model(), messages=[], payload=payload, stream=True, provider="openai"
        )
    assert kwargs["max_tokens"] == expected
