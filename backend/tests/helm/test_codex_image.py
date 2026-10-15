"""Helm value agents.codexImage sets CODEX_IMAGE on API and worker pods."""

from __future__ import annotations

from typing import Iterator

import yaml

from tests.helm.chart_helpers import helm_template, load_values, resolve_values_path

_PIN = "foo@sha256:abc"
_TEMPLATES = (
    "templates/api-deployment.yaml",
    "templates/spacesync-worker-deployment.yaml",
)


def _documents(rendered: str) -> Iterator[dict]:
    for doc in yaml.safe_load_all(rendered):
        if isinstance(doc, dict):
            yield doc


def _codex_image_values(rendered: str) -> list[str | None]:
    found: list[str | None] = []
    for doc in _documents(rendered):
        containers = doc["spec"]["template"]["spec"]["containers"]
        for container in containers:
            env = container.get("env") or []
            match = next(
                (item for item in env if item.get("name") == "CODEX_IMAGE"),
                None,
            )
            found.append(None if match is None else match.get("value"))
    return found


def test_values_leave_codex_image_empty() -> None:
    values = load_values()
    assert resolve_values_path(values, "agents.codexImage") == ""


def test_unset_codex_image_renders_no_env() -> None:
    for template in _TEMPLATES:
        rendered = helm_template(template)
        assert "CODEX_IMAGE" not in rendered
        assert all(value is None for value in _codex_image_values(rendered))


def test_codex_image_renders_on_api_and_workers() -> None:
    for template in _TEMPLATES:
        rendered = helm_template(
            template,
            string_overrides=[f"agents.codexImage={_PIN}"],
        )
        values = _codex_image_values(rendered)
        assert values, template
        assert values == [_PIN] * len(values)


def test_codex_image_is_not_set_on_the_gateway() -> None:
    rendered = helm_template(
        "templates/gateway-deployment.yaml",
        string_overrides=[f"agents.codexImage={_PIN}"],
    )
    assert "CODEX_IMAGE" not in rendered
