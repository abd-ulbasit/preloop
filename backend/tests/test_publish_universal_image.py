"""Publish workflow for the universal Codex image (issue #1716).

The image build needs Docker and a large disk. These tests pin the publish
gate and the workflow contract: pull requests smoke and do not push, and a
main dispatch, a recipe merge, and a version tag publish the index digest.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "publish-universal-image.yml"
GATE = REPO / "environments" / "universal" / "publish-gate.sh"
README = REPO / "environments" / "universal" / "README.md"
BASH = shutil.which("bash") or "/bin/bash"
SHA_PIN = re.compile(r"@[0-9a-f]{40}( # v\d|$)")
RECIPE_PATHS = {
    "environments/universal/Dockerfile",
    "environments/universal/*.sh",
    "environments/universal/*.py",
    "environments/universal/fixtures/**",
    "environments/perl/perl-toolchain-smoke.sh",
    "environments/perl/fixtures/**",
    ".github/workflows/publish-universal-image.yml",
}


def _workflow() -> dict[str, object]:
    loaded = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _trigger(workflow: dict[str, object]) -> dict[str, object]:
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _jobs(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    steps = job["steps"]
    assert isinstance(steps, list)
    return steps


def _gate(**env: str) -> tuple[str, str]:
    result = subprocess.run(
        [BASH, str(GATE)],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "GITHUB_EVENT_NAME": env.get("GITHUB_EVENT_NAME", ""),
            "GITHUB_REF": env.get("GITHUB_REF", ""),
            "GITHUB_REF_TYPE": env.get("GITHUB_REF_TYPE", ""),
            "GITHUB_REF_NAME": env.get("GITHUB_REF_NAME", ""),
            "RECIPE": env.get("RECIPE", ""),
        },
    )
    assert result.returncode == 0, result.stderr
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    return values["publish"], values["run"]


def test_gate_script_passes_bash_n() -> None:
    result = subprocess.run(
        [BASH, "-n", str(GATE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_pull_request_and_schedule_build_without_publishing() -> None:
    assert _gate(GITHUB_EVENT_NAME="pull_request", GITHUB_REF="refs/pull/12/merge") == (
        "false",
        "true",
    )
    assert _gate(GITHUB_EVENT_NAME="schedule", GITHUB_REF="refs/heads/main") == (
        "false",
        "true",
    )


def test_workflow_dispatch_publishes_only_on_main() -> None:
    assert _gate(
        GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/main"
    ) == ("true", "true")
    assert _gate(
        GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/feature"
    ) == ("false", "true")


def test_main_push_publishes_only_when_the_recipe_changed() -> None:
    assert _gate(
        GITHUB_EVENT_NAME="push",
        GITHUB_REF="refs/heads/main",
        RECIPE="true",
    ) == ("true", "true")
    assert _gate(
        GITHUB_EVENT_NAME="push",
        GITHUB_REF="refs/heads/main",
        RECIPE="false",
    ) == ("false", "false")
    assert _gate(GITHUB_EVENT_NAME="push", GITHUB_REF="refs/heads/main") == (
        "false",
        "false",
    )


def test_version_tags_publish_and_other_tags_do_not_run() -> None:
    assert _gate(
        GITHUB_EVENT_NAME="push",
        GITHUB_REF="refs/tags/v1.2.3",
        GITHUB_REF_TYPE="tag",
        GITHUB_REF_NAME="v1.2.3",
    ) == ("true", "true")
    assert _gate(
        GITHUB_EVENT_NAME="push",
        GITHUB_REF="refs/tags/other",
        GITHUB_REF_TYPE="tag",
        GITHUB_REF_NAME="other",
    ) == ("false", "false")


def test_workflow_triggers_and_image_name() -> None:
    workflow = _workflow()
    trigger = _trigger(workflow)
    assert trigger["push"] == {"branches": ["main"], "tags": ["v*"]}
    assert "workflow_dispatch" in trigger
    cron = trigger["schedule"]
    assert isinstance(cron, list)
    assert cron == [{"cron": "23 6 * * 1"}]
    paths = trigger["pull_request"]["paths"]
    assert set(paths) == RECIPE_PATHS
    detect = next(
        step
        for step in _steps(_jobs(workflow)["gate"])
        if step.get("name") == "Detect recipe changes"
    )
    listed = set(re.findall(r"- '([^']+)'", str(detect["with"]["filters"])))
    assert listed == RECIPE_PATHS
    assert workflow["env"]["IMAGE"] == (
        "ghcr.io/${{ github.repository_owner }}/preloop-codex-universal"
    )
    assert workflow["permissions"] == {"contents": "read"}


def test_actions_are_pinned_by_sha() -> None:
    text = WORKFLOW.read_text()
    uses = re.findall(r"uses: (\S+)", text)
    assert uses
    for use in uses:
        assert SHA_PIN.search(use), use


def test_smoke_runs_on_amd64_before_any_push() -> None:
    workflow = _workflow()
    steps = _steps(_jobs(workflow)["image"])
    names = [step.get("name") for step in steps]
    assert names.index("Free disk space") < names.index("Build amd64 image")
    assert names.index("Build amd64 image") < names.index("Smoke offline")
    assert names.index("Smoke offline") < names.index("Push smoked amd64 image")
    smoke = steps[names.index("Smoke offline")]
    assert "run-smoke.sh" in str(smoke["run"])
    assert smoke["if"] == "matrix.arch == 'amd64'"
    free = steps[names.index("Free disk space")]
    assert "/usr/local/lib/android" in str(free["run"])
    assert "40 * 1024 * 1024" in str(free["run"])
    matrix = _jobs(workflow)["image"]["strategy"]["matrix"]["include"]
    assert {row["platform"] for row in matrix} == {"linux/amd64", "linux/arm64"}
    assert {row["runner"] for row in matrix} == {"ubuntu-latest", "ubuntu-24.04-arm"}
    build_steps = [
        step
        for step in steps
        if "docker/build-push-action" in str(step.get("uses") or "")
    ]
    assert build_steps
    for step in build_steps:
        assert step["with"]["platforms"] == "${{ matrix.platform }}"


def test_pull_request_path_never_sets_push_true_without_the_gate() -> None:
    workflow = _workflow()
    image = _jobs(workflow)["image"]
    assert image["if"] == "needs.gate.outputs.run == 'true'"
    for step in _steps(image):
        pushed = step.get("with", {}).get("push") is True
        script_pushes = "docker push" in str(step.get("run") or "")
        if not pushed and not script_pushes:
            continue
        guard = str(step.get("if"))
        assert "needs.gate.outputs.publish == 'true'" in guard
    for step in _steps(image):
        if "docker/build-push-action" in str(step.get("uses") or ""):
            assert step["with"]["provenance"] is False
    publish = _jobs(workflow)["publish"]
    assert publish["if"] == (
        "needs.gate.outputs.publish == 'true' && needs.image.result == 'success'"
    )
    text = WORKFLOW.read_text()
    assert "publish-gate.sh" in text
    assert 'gate_out="$(bash environments/universal/publish-gate.sh)"' in text
    assert "publish-gate.sh failed" in text
    assert "did not emit publish and run" in text
    assert "done < <(" not in text


def test_index_digest_is_summarized_and_attested() -> None:
    workflow = _workflow()
    publish = _jobs(workflow)["publish"]
    permissions = publish["permissions"]
    assert permissions["id-token"] == "write"
    assert permissions["attestations"] == "write"
    assert permissions["packages"] == "write"
    steps = _steps(publish)
    index = next(step for step in steps if step.get("name") == "Publish index")
    script = str(index["run"])
    assert "imagetools create" in script
    assert "sha-${SHORT}" in script
    assert "${IMAGE}:${GITHUB_REF_NAME}" in script
    assert ".Manifest.Digest" in script
    assert "${IMAGE}@${digest}" in script
    assert "^v[0-9A-Za-z._-]+$" in script
    assert "._+-" not in script
    attest = next(step for step in steps if step.get("name") == "Attest index digest")
    assert attest["uses"].startswith("actions/attest-build-provenance@")
    assert attest["with"]["push-to-registry"] is True
    assert attest["with"]["subject-digest"] == "${{ steps.index.outputs.digest }}"
    assert attest["with"]["subject-name"] == "${{ env.IMAGE }}"
    public = next(step for step in steps if step.get("name") == "Make package public")
    assert "visibility=public" in str(public["run"])


def test_environments_doc_names_the_published_digest() -> None:
    text = (
        REPO / "docs" / "guide" / "flows" / "environments-and-recovery.md"
    ).read_text()
    assert "publish-universal-image.yml" in text
    assert (
        "ghcr.io/preloop/preloop-codex-universal@sha256:"
        "2ce76f7bab752582f34122bc714ba48db1e3b5964a91cd1e0e496c8461eaf6de"
    ) in text
    assert "agents.codexImage" in text
    assert "hosted default stays" not in text


def test_readme_points_at_the_publish_workflow() -> None:
    collapsed = " ".join(README.read_text().split())
    assert ".github/workflows/publish-universal-image.yml" in collapsed
    assert "sha-<12" in collapsed
    assert "do not push" in collapsed
    assert "hosted default" in collapsed
