"""Guards for the release helper's pre-push and pre-exit checks."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_script", REPO_ROOT / "scripts" / "release.py"
)
assert SPEC is not None and SPEC.loader is not None
release_script = importlib.util.module_from_spec(SPEC)
sys.modules["release_script"] = release_script
SPEC.loader.exec_module(release_script)

# The v0.16.0 create-release job passed the changelog through the environment
# and published from an inline body. Inlined so CI does not need that tag.
_V0_16_CREATE_RELEASE = """
  create-release:
    if: startsWith(github.ref, 'refs/tags/v')
    permissions:
      contents: write
      id-token: write
      attestations: write
    steps:
      - name: Finalize changelog
        env:
          CHANGELOG_CONTENT: ${{ steps.changelog.outputs.content }}
        run: printf '%s\\n' "$CHANGELOG_CONTENT"
      - uses: actions/attest-build-provenance@abc
        with:
          subject-path: 'release-assets/*'
      - run: python3 scripts/release_assets.py verify release-assets
      - run: cp "$BUNDLE_PATH" "release-assets/preloop-${GITHUB_REF_NAME}.intoto.jsonl"
      - uses: softprops/action-gh-release@abc
        with:
          files: release-assets/*
          body: |
            notes
          draft: false
"""


def _workflow_text() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )


def _create_release_job() -> str:
    return release_script.extract_job(_workflow_text(), "create-release")


def test_current_workflow_can_publish_signed_assets() -> None:
    """The checkout's create-release job passes the pre-push check."""
    assert release_script.workflow_release_problems(_create_release_job()) == []


def test_package_version_comes_from_the_release_helper() -> None:
    """The workflow must not keep a second PEP 440 implementation."""
    job = _create_release_job()
    assert "from release import python_package_version" in job
    assert "label_map" not in job


def test_extract_job_stops_at_the_job_boundary() -> None:
    """The checker must not treat another job's steps as create-release."""
    job = _create_release_job()
    assert job.startswith("  create-release:\n")
    assert "\n  build-backend:\n" not in job
    assert "\n  sbom:\n" not in job


def test_changelog_in_the_environment_is_rejected() -> None:
    """v0.16.0 skipped attestation because the changelog was an env var."""
    job = _create_release_job().replace(
        "body_path:",
        "CHANGELOG_CONTENT: ${{ steps.changelog.outputs.content }}\n          body:",
    )
    problems = release_script.workflow_release_problems(job)
    assert any("changelog text" in item for item in problems)
    assert any("body_path" in item for item in problems)


def test_v0_16_0_tag_workflow_is_rejected() -> None:
    """The v0.16.0 release job shape fails the local check without that tag."""
    problems = release_script.workflow_release_problems(_V0_16_CREATE_RELEASE)
    assert any("changelog text" in item for item in problems)


def test_immediate_publish_is_rejected() -> None:
    """A public release must not be created before the bundle is attached."""
    job = _create_release_job().replace("draft: true", "draft: false", 1)
    problems = release_script.workflow_release_problems(job)
    assert any("publishes immediately" in item for item in problems)
    assert any("stay a draft" in item for item in problems)


def test_upload_before_attestation_is_rejected() -> None:
    """A release that uploads first can ship bytes with no provenance file."""
    job = """
  create-release:
    permissions:
      id-token: write
      attestations: write
    steps:
      - uses: softprops/action-gh-release@abc
        with:
          files: release-assets/*
          body_path: release-notes.md
      - uses: actions/attest-build-provenance@abc
        with:
          subject-path: 'release-assets/*'
      - run: cp bundle release-assets/preloop-${GITHUB_REF_NAME}.intoto.jsonl
      - run: python3 scripts/release_assets.py verify release-assets
"""
    problems = release_script.workflow_release_problems(job)
    assert any("before Create Release" in item for item in problems)


def test_tag_run_is_not_confused_with_the_main_run() -> None:
    """The same commit's green main run must not satisfy the tag check."""
    sha = "a" * 40
    runs = [
        {
            "databaseId": 1,
            "headSha": sha,
            "headBranch": "main",
            "event": "push",
            "conclusion": "success",
        },
        {
            "databaseId": 2,
            "headSha": sha,
            "headBranch": "v0.16.0",
            "event": "push",
            "conclusion": "failure",
        },
    ]
    selected = release_script.select_tag_push_run(runs, sha, "v0.16.0")
    assert selected is not None
    assert selected["databaseId"] == 2
    assert release_script.select_tag_push_run(runs, sha, "v0.17.0") is None


def test_skipped_create_release_job_is_a_failure() -> None:
    """A green workflow that never ran Create Release did not publish."""
    problem = release_script.release_run_problem(
        {"conclusion": "success"},
        [{"name": "Create Release", "conclusion": "skipped"}],
    )
    assert problem == "Create Release conclusion=skipped"


def test_prerelease_asset_names_use_pep440_packages() -> None:
    """Wheels use PEP 440 names; the provenance file keeps the git tag."""
    names = release_script.expected_release_assets("0.8.0-beta.1")
    assert "preloop-v0.8.0-beta.1.intoto.jsonl" in names
    assert "preloop-0.8.0b1-py3-none-any.whl" in names
    assert "preloop-0.8.0b1.tar.gz" in names
    assert "preloop-0.8.0-beta.1.tgz" in names


def test_published_release_must_include_provenance() -> None:
    """Missing the bundle is a failed release even when every binary is there."""
    version = "1.2.3"
    names = release_script.expected_release_assets(
        version, release_script.vex_asset_names()
    )
    payload = {
        "isDraft": False,
        "assets": [
            {"name": name} for name in names if not name.endswith(".intoto.jsonl")
        ],
    }
    problem = release_script.published_release_problem(version, payload)
    assert problem is not None
    assert "preloop-v1.2.3.intoto.jsonl" in problem

    complete = {"isDraft": False, "assets": [{"name": name} for name in names]}
    assert release_script.published_release_problem(version, complete) is None


def test_draft_release_is_not_done() -> None:
    """A draft is not a published signed release."""
    problem = release_script.published_release_problem("1.2.3", {"isDraft": True})
    assert problem == "v1.2.3 is still a draft"
