"""Execution configuration reaches each lifecycle consumer with read-only keys."""

import yaml

from .chart_helpers import helm_template_all


def test_remote_execution_secret_and_settings():
    rendered = helm_template_all(
        [
            "agentExecution.kubeconfigSecret=example-execution",
            "agentExecution.namespace.create=false",
            "agentExecution.namespace.name=example-remote-agents",
            "agentExecution.priorityClassName=example-flow-prod",
            "agentExecution.apiUrl=https://example.com",
            "agentExecution.gatewayUrl=https://example.com/openai/v1",
        ]
    )
    count = 0
    for resource in yaml.safe_load_all(rendered):
        if not resource or resource.get("kind") != "Deployment":
            continue
        for container in resource["spec"]["template"]["spec"]["containers"]:
            env = {
                entry["name"]: entry.get("value") for entry in container.get("env", [])
            }
            if "PRELOOP_EXECUTION_KUBECONFIG" not in env:
                continue
            count += 1
            assert env["AGENT_EXECUTION_NAMESPACE"] == "example-remote-agents"
            assert (
                env["PRELOOP_EXECUTION_KUBECONFIG"]
                == "/var/run/preloop/execution/kubeconfig"
            )
            assert env["PRELOOP_EXECUTION_PRIORITY_CLASS"] == "example-flow-prod"
            mount = next(
                item
                for item in container["volumeMounts"]
                if item["name"] == "execution-kubeconfig"
            )
            assert mount["readOnly"] is True
            volumes = resource["spec"]["template"]["spec"]["volumes"]
            volume = next(
                item for item in volumes if item["name"] == "execution-kubeconfig"
            )
            assert volume["secret"]["secretName"] == "example-execution"
    assert count >= 3


def test_remote_execution_does_not_provision_local_agent_resources():
    rendered = helm_template_all(
        [
            "agentExecution.kubeconfigSecret=example-execution",
            "agentExecution.namespace.create=true",
            "agentExecution.namespace.name=example-remote-agents",
            "agentExecution.rbac.create=true",
            "agentExecution.networkPolicy.enabled=true",
            "agentExecution.networkPolicy.cilium.enabled=true",
        ]
    )
    for resource in yaml.safe_load_all(rendered):
        if not resource:
            continue
        assert resource.get("kind") not in {"Namespace", "ResourceQuota", "LimitRange"}
        labels = resource.get("metadata", {}).get("labels", {})
        assert labels.get("app.kubernetes.io/component") != "agent-execution"
