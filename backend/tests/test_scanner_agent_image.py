"""The opt-in scanner agent image recipe (issue #705).

The image is built and smoked by environments/scanner/run-smoke.sh and the
Publish Scanner Image workflow, which need Docker. These tests pin the
recipe's contract, the manifest writer and the profile registration.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from preloop.agents.images import DEFAULT_AGENT_IMAGES
from preloop.services.flow_environment import EnvironmentProfile, profile_setup_shell

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "environments" / "scanner"
DOCKERFILE = SCANNER / "Dockerfile"
WORKFLOW = REPO / ".github" / "workflows" / "publish-scanner-image.yml"
DB = "ghcr.io/aquasecurity/trivy-db@sha256:" + "a" * 64


def _load_write_manifest() -> ModuleType:
    """Load the scanner manifest writer under a name unique to this file.

    A second test module also has a ``write_manifest.py``. Importing both as
    the top-level name ``write_manifest`` makes ``sys.modules`` keep whichever
    loaded first.
    """
    path = SCANNER / "write_manifest.py"
    spec = importlib.util.spec_from_file_location("scanner_write_manifest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


write_manifest = _load_write_manifest()


def _arg_default(name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE.read_text(), re.M)
    assert match, name
    return match.group(1)


def test_base_is_pinned_upstream_and_hosted_default_is_separate() -> None:
    """The scanner still extends upstream. Hosted Codex uses our image."""
    base = _arg_default("CODEX_BASE_IMAGE")
    name, _, digest = base.partition("@sha256:")
    assert re.fullmatch(r"[a-f0-9]{64}", digest)
    assert name == "ghcr.io/openai/codex-universal"
    hosted = DEFAULT_AGENT_IMAGES["codex"]
    assert re.fullmatch(
        r"ghcr.io/preloop/preloop-codex-universal@sha256:[a-f0-9]{64}",
        hosted,
    )
    assert hosted.split("@", 1)[0] != name
    assert re.fullmatch(r"\S+@sha256:[a-f0-9]{64}", _arg_default("FETCH_IMAGE"))


def test_trivy_is_pinned_and_checksummed_and_db_has_no_default() -> None:
    text = DOCKERFILE.read_text()
    assert _arg_default("TRIVY_VERSION") == "0.74.0"
    for arch in ("AMD64", "ARM64"):
        assert re.fullmatch(r"[a-f0-9]{64}", _arg_default(f"TRIVY_SHA256_{arch}"))
    assert "sha256sum -c -" in text
    assert re.search(r"^ARG TRIVY_DB_REPOSITORY$", text, re.M)
    assert '--db-repository "${TRIVY_DB_REPOSITORY}"' in text
    assert "*@sha256:*" in text, "mutable tags must be refused"
    assert "ENV TRIVY_CACHE_DIR=/opt/trivy-cache" in text


def test_final_stage_runs_nothing_on_the_base() -> None:
    """COPY --link only, so BuildKit can push without unpacking 10 GB."""
    final = DOCKERFILE.read_text().split("FROM ${CODEX_BASE_IMAGE}\n", 1)[1]
    instructions = {
        line.split()[0] for line in final.splitlines() if line and line[0].isupper()
    }
    assert instructions <= {"ARG", "COPY", "ENV", "LABEL"}
    assert all(
        "--link" in line for line in final.splitlines() if line.startswith("COPY")
    )


def test_environment_marker_satisfies_the_setup_protocol_check(tmp_path: Path) -> None:
    marker = json.loads((SCANNER / "preloop-environment.json").read_text())
    assert marker == {"version": 1, "harness": "codex"}
    profile = EnvironmentProfile(
        image="x@sha256:" + "0" * 64, harness="codex", required_executables=[]
    )
    shell = profile_setup_shell(profile, kubernetes=False, working_dir=str(tmp_path))
    wrapper = shlex.split(shell.split("&& ", 1)[1])[-1]
    # Run the exact check the setup wrapper applies to the marker file.
    check = next(line for line in wrapper.splitlines() if "protocol.get(" in line)
    code = check.strip().replace("raise ValueError()", "raise AssertionError()")
    exec(code, {}, {"protocol": marker})
    with pytest.raises(AssertionError):
        exec(code, {}, {"protocol": {"version": 1, "harness": "pi"}})
    assert (
        "COPY --link --chmod=0644 environments/scanner/preloop-environment.json /opt/preloop-environment.json"
        in DOCKERFILE.read_text()
    )


def test_example_profile_validates_and_selects_codex() -> None:
    registry = json.loads((SCANNER / "profile.example.json").read_text())
    profile = EnvironmentProfile.model_validate(registry["codex-scanner"])
    assert profile.harness == "codex"
    assert "trivy" in profile.required_executables
    assert profile.env["TRIVY_CACHE_DIR"] == "/opt/trivy-cache"


def _cache(tmp_path: Path, metadata: object) -> Path:
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "metadata.json").write_text(json.dumps(metadata))
    return tmp_path


def test_manifest_records_version_digest_and_updated_at(tmp_path: Path) -> None:
    cache = _cache(tmp_path, {"Version": 2, "UpdatedAt": "2026-10-11T07:05:14Z"})
    out = tmp_path / "preloop-scanners.json"
    code = write_manifest.main(
        [
            "--version=0.74.0",
            f"--db-repository={DB}",
            f"--cache-dir={cache}",
            f"--output={out}",
            "--baked-at=2026-10-11T12:00:00Z",
        ]
    )
    assert code == 0
    assert json.loads(out.read_text()) == {
        "trivy": {
            "version": "0.74.0",
            "db_repository": DB,
            "db_updated_at": "2026-10-11T07:05:14Z",
            "baked_at": "2026-10-11T12:00:00Z",
        }
    }


@pytest.mark.parametrize(
    ("version", "db", "metadata"),
    [
        ("0.74.0", "ghcr.io/aquasecurity/trivy-db:2", {"UpdatedAt": "x"}),
        ("latest", DB, {"UpdatedAt": "x"}),
        ("0.74.0", DB, {"Version": 2}),
    ],
)
def test_manifest_refuses_unpinned_or_incomplete_inputs(
    tmp_path: Path, version: str, db: str, metadata: dict[str, object]
) -> None:
    cache = _cache(tmp_path, metadata)
    with pytest.raises(ValueError):
        write_manifest.build_manifest(
            version=version, db_repository=db, cache_dir=cache, baked_at="t"
        )


def test_manifest_cli_exits_nonzero_without_metadata(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCANNER / "write_manifest.py"),
            "--version=0.74.0",
            f"--db-repository={DB}",
            f"--cache-dir={tmp_path}",
            f"--output={tmp_path / 'out.json'}",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert not (tmp_path / "out.json").exists()


def test_workflow_publishes_on_version_tags_only() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["push"] == {"tags": ["v*"]}
    steps = workflow["jobs"]["scanner-image"]["steps"]
    guarded = [
        s
        for s in steps
        if s.get("name") in ("Log in to Container Registry", "Build and push")
    ]
    assert len(guarded) == 2
    for step in guarded:
        assert step["if"] == "startsWith(github.ref, 'refs/tags/v')"
    for step in steps:
        if "docker/build-push-action" in step.get("uses", ""):
            assert step is guarded[1]
    smoke = next(s for s in steps if s.get("name") == "Smoke offline")
    assert "run-smoke.sh" in smoke["run"]
    assert workflow["env"]["SMOKE_BASE"] == _arg_default("FETCH_IMAGE")


def test_smoke_runs_trivy_offline_from_the_baked_db() -> None:
    text = (SCANNER / "run-smoke.sh").read_text()
    assert "--network none" in text
    assert "--skip-db-update --skip-java-db-update" in text
    assert "/opt/preloop-scanner/fixtures/vulnerable" in text


def test_vulnerable_fixture_is_baked_into_the_image_not_committed() -> None:
    """Scorecard scans every *requirements*.txt in the git tree.

    Django 2.2.0 and requests 2.19.0 (and the urllib3 and idna those pins
    pull in) were the entire OpenSSF Vulnerabilities finding list. The
    offline smoke test still needs that file inside the image.
    """
    dockerfile = DOCKERFILE.read_text()
    assert "django==2.2.0" in dockerfile
    assert "requests==2.19.0" in dockerfile
    assert "/out/opt/preloop-scanner/fixtures/vulnerable/requirements.txt" in dockerfile
    assert list(SCANNER.rglob("*requirements*.txt")) == []


def test_latest_moves_on_stable_tags_only() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    push = next(
        s
        for s in workflow["jobs"]["scanner-image"]["steps"]
        if s.get("name") == "Build and push"
    )
    tags = [line for line in push["with"]["tags"].splitlines() if line]
    assert tags[0] == "${{ env.IMAGE }}:${{ github.ref_name }}"
    assert tags[1] == (
        "${{ !contains(github.ref_name, '-') && "
        "format('{0}:latest', env.IMAGE) || '' }}"
    )


def test_pull_requests_touching_the_recipe_build_and_smoke() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers["pull_request"]["paths"]) == {
        "environments/scanner/**",
        ".github/workflows/publish-scanner-image.yml",
    }
