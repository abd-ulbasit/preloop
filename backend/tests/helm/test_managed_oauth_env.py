"""Chart wiring for the managed Bitbucket Cloud and Atlassian OAuth consumers."""

from __future__ import annotations

import pytest
import yaml

from tests.helm.chart_helpers import helm_template, load_values, resolve_values_path

DEPLOYMENTS = (
    "templates/api-deployment.yaml",
    "templates/gateway-deployment.yaml",
    "templates/spacesync-worker-deployment.yaml",
)
PROVIDERS = (
    ("bitbucketOauth", "BITBUCKET_OAUTH", "bitbucket-oauth-client-secret"),
    ("atlassianOauth", "ATLASSIAN_OAUTH", "atlassian-oauth-client-secret"),
)


def _envs(rendered: str) -> list[dict]:
    envs: list[dict] = []
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            envs.append({e["name"]: e for e in container.get("env") or []})
    return envs


@pytest.mark.parametrize("key,_prefix,_secret", PROVIDERS)
def test_values_default_disabled(key: str, _prefix: str, _secret: str) -> None:
    values = load_values()
    assert resolve_values_path(values, f"{key}.enabled") is False
    assert resolve_values_path(values, f"{key}.clientSecret") == ""


@pytest.mark.parametrize("template", DEPLOYMENTS)
def test_env_absent_when_disabled(template: str) -> None:
    rendered = helm_template(template)
    assert "BITBUCKET_OAUTH" not in rendered
    assert "ATLASSIAN_OAUTH" not in rendered


@pytest.mark.parametrize("template", DEPLOYMENTS)
@pytest.mark.parametrize("key,prefix,secret", PROVIDERS)
def test_env_present_when_enabled(
    template: str, key: str, prefix: str, secret: str
) -> None:
    rendered = helm_template(
        template,
        [
            f"{key}.enabled=true",
            f"{key}.clientId=client-id-value",
            f"{key}.clientSecret=never-inline",
            f"{key}.callbackUrl=https://preloop.example/cb",
        ],
    )
    envs = _envs(rendered)
    assert envs
    for env in envs:
        assert env[f"{prefix}_CLIENT_ID"]["value"] == "client-id-value"
        ref = env[f"{prefix}_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]
        assert ref["key"] == secret
        assert env[f"{prefix}_CALLBACK_URL"]["value"] == "https://preloop.example/cb"
        assert f"{prefix}_SCOPES" not in env
    assert "never-inline" not in rendered


@pytest.mark.parametrize("key,_prefix,secret", PROVIDERS)
def test_secret_key_only_when_enabled(key: str, _prefix: str, secret: str) -> None:
    assert secret not in helm_template("templates/secret.yaml")
    rendered = helm_template(
        "templates/secret.yaml", [f"{key}.enabled=true", f"{key}.clientSecret=s"]
    )
    assert f"{secret}:" in rendered
