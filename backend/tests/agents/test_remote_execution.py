"""Execution credentials must not redirect unrelated Kubernetes clients."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from kubernetes_asyncio import client

from preloop.agents.container import ContainerAgentExecutor
from preloop.services.model_runtime_resolver import default_model_gateway_url


@pytest.mark.asyncio
async def test_explicit_execution_config_precedes_incluster_and_is_private(monkeypatch):
    monkeypatch.setenv("PRELOOP_EXECUTION_KUBECONFIG", "/execution/kubeconfig")
    original = client.Configuration.get_default_copy()
    executor = ContainerAgentExecutor(
        "codex", {}, "example:latest", use_kubernetes=True
    )
    load = AsyncMock()
    with (
        patch("preloop.agents.container.config.load_kube_config", load),
        patch(
            "preloop.agents.container.config.load_incluster_config", Mock()
        ) as incluster,
    ):
        await executor._init_kubernetes_clients()
        await executor._init_kubernetes_clients()
        incluster.assert_not_called()
        load.assert_awaited_once()
        assert load.call_args.kwargs["config_file"] == "/execution/kubeconfig"
        configuration = load.call_args.kwargs["client_configuration"]
        assert executor._k8s_api_client.configuration is configuration
        assert executor._k8s_batch_api.api_client is executor._k8s_api_client
        assert executor._k8s_core_api.api_client is executor._k8s_api_client
        assert client.Configuration.get_default_copy().host == original.host
    await executor.aclose()
    assert executor._k8s_api_client is None
    assert not executor._k8s_initialized


@pytest.mark.asyncio
async def test_invalid_explicit_config_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("PRELOOP_EXECUTION_KUBECONFIG", "/invalid/kubeconfig")
    executor = ContainerAgentExecutor(
        "codex", {}, "example:latest", use_kubernetes=True
    )
    with (
        patch(
            "preloop.agents.container.config.load_kube_config",
            AsyncMock(side_effect=ValueError),
        ),
        patch(
            "preloop.agents.container.config.load_incluster_config", Mock()
        ) as incluster,
    ):
        with pytest.raises(ValueError):
            await executor._init_kubernetes_clients()
        incluster.assert_not_called()
        assert not executor._k8s_initialized


def test_remote_gateway_precedes_cluster_local_settings(monkeypatch):
    monkeypatch.setenv("PRELOOP_EXECUTION_GATEWAY_URL", "https://example.com/openai/v1")
    monkeypatch.setenv("PRELOOP_MODEL_GATEWAY_URL_K8S", "http://gateway.svc/openai/v1")
    assert default_model_gateway_url() == "https://example.com/openai/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["codex", "gemini", "opencode"])
async def test_remote_mcp_wins_after_allowed_tool_environment_merge(
    monkeypatch, agent_name
):
    import importlib
    import json

    classes = {
        "codex": "CodexAgent",
        "gemini": "GeminiAgent",
        "opencode": "OpenCodeAgent",
    }
    module = importlib.import_module("preloop.agents." + agent_name)
    agent = getattr(module, classes[agent_name])({})
    monkeypatch.setenv("PRELOOP_EXECUTION_API_URL", "https://example.com")
    monkeypatch.setenv("PRELOOP_URL", "http://control-plane.svc")
    monkeypatch.setenv("PRELOOP_MCP_URL_K8S", "http://api.svc/mcp/v1")
    monkeypatch.setattr(
        agent, "_build_" + agent_name + "_script", Mock(return_value="true")
    )
    monkeypatch.setattr(agent, "_prepare_environment", AsyncMock(return_value={}))
    context = {
        "allowed_mcp_servers": ["preloop-mcp"],
        "account_api_token": "synthetic-token",
    }
    with patch.object(
        ContainerAgentExecutor, "_start_kubernetes_pod", AsyncMock(return_value="job")
    ):
        await agent._start_kubernetes_pod(context)
    env = context["_codex_env" if agent_name == "codex" else "_agent_env"]
    assert env["PRELOOP_MCP_URL"] == "https://example.com/mcp/v1"
    config = json.loads(env["MCP_CONFIG_JSON"])
    assert config["mcpServers"]["preloop-mcp"]["url"] == "https://example.com/mcp/v1"


@pytest.mark.asyncio
async def test_final_job_environment_keeps_remote_api(monkeypatch):
    import json

    monkeypatch.setenv("PRELOOP_EXECUTION_API_URL", "https://example.com")
    monkeypatch.setenv("PRELOOP_URL", "http://control-plane.svc")
    executor = ContainerAgentExecutor(
        "codex", {}, "example:latest", use_kubernetes=True
    )
    monkeypatch.setattr(executor, "_init_kubernetes_clients", AsyncMock())
    create = AsyncMock(return_value="job")
    monkeypatch.setattr(executor, "_create_kubernetes_job", create)
    await executor._start_kubernetes_pod(
        {
            "execution_id": "00000000-0000-0000-0000-000000000001",
            "flow_id": "00000000-0000-0000-0000-000000000002",
            "prompt": "synthetic prompt",
            "allowed_mcp_servers": ["preloop-mcp"],
        }
    )
    job = create.call_args.args[0]
    env = {item.name: item.value for item in job.spec.template.spec.containers[0].env}
    assert env["PRELOOP_MCP_URL"] == "https://example.com/mcp/v1"
    config = json.loads(env["MCP_CONFIG_JSON"])
    assert config["mcpServers"]["preloop-mcp"]["url"] == "https://example.com/mcp/v1"
