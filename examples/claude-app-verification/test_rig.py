"""Fixture-only tests. These cannot establish app verification."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from rig import CASES, Fixture, assess, bundle_digest, events, fresh


def test_image_installs_the_locked_claude_code_with_npm_ci() -> None:
    """Scorecard treats `npm install` as unpinned; `npm ci` checks the lock."""
    root = Path(__file__).resolve().parent
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "npm install" not in dockerfile
    assert "npm ci --omit=dev --prefix /opt/claude-code" in dockerfile
    assert "rm -rf /usr/local/lib/node_modules" not in dockerfile
    assert (
        "cp -a /opt/claude-code/node_modules/@anthropic-ai /usr/local/lib/node_modules/"
    ) in dockerfile
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["@anthropic-ai/claude-code"] == "2.1.296"
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    locked = lock["packages"]["node_modules/@anthropic-ai/claude-code"]
    assert locked["version"] == "2.1.296"
    assert str(locked["integrity"]).startswith("sha512-")


def test_denied_marker_absence_without_app_is_not_a_pass(tmp_path: Path) -> None:
    with Fixture(tmp_path / "marker", 200, "deny") as fixture:
        assert assess("deny", fixture, fixture.marker, 0)["result"] == "failed"


@pytest.mark.parametrize("name", CASES)
def test_case_requires_tool_roundtrip_and_correlated_hook(
    name: str, tmp_path: Path
) -> None:
    with Fixture(tmp_path / "marker", *CASES[name][:2]) as fixture:
        fixture.tool_sent = fixture.tool_returned = True
        if CASES[name][3]:
            fixture.marker.write_text("fixture")
        if name != "alternate_profile":
            fixture.observations.append(
                {
                    "path": "/api/v1/agents/permission-check",
                    "input": {
                        "tool_name": "Bash",
                        "tool_input": {"command": f"printf fixture > {fixture.marker}"},
                    },
                }
            )
        assert assess(name, fixture, fixture.marker, 0)["result"] in (
            "passed",
            "boundary_gap",
        )
        fixture.tool_returned = False
        assert assess(name, fixture, fixture.marker, 0)["result"] == "failed"


def test_deny_side_effect_and_wrong_hook_fail(tmp_path: Path) -> None:
    with Fixture(tmp_path / "marker", 200, "deny") as fixture:
        fixture.tool_sent = fixture.tool_returned = True
        fixture.observations.append(
            {"path": "/api/v1/agents/permission-check", "input": {"tool_name": "Read"}}
        )
        assert assess("deny", fixture, fixture.marker, 0)["result"] == "failed"
        fixture.marker.touch()
        assert assess("deny", fixture, fixture.marker, 0)["result"] == "failed"


def test_stream_has_complete_tool_input_and_terminal_reason() -> None:
    content = {
        "type": "tool_use",
        "id": "tool_fixture",
        "name": "Bash",
        "input": {"command": "printf fixture"},
    }
    stream = events(content, "fixture-model")
    assert json.loads(stream[2][1]["delta"]["partial_json"]) == content["input"]
    assert stream[-2][1]["delta"]["stop_reason"] == "tool_use"
    assert (
        events({"type": "text", "text": "done"}, "fixture-model")[-2][1]["delta"][
            "stop_reason"
        ]
        == "end_turn"
    )


def test_evidence_expiry_revision_and_source() -> None:
    now = datetime.now(timezone.utc)
    revisions = {
        key: "fixture-v1"
        for key in ("app", "cli", "config", "policy", "credential", "rig")
    }
    report = {
        "schema_version": 1,
        "evidence_kind": "app_harness",
        "revisions": revisions,
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(),
    }
    assert fresh(report, revisions, now)
    assert not fresh(report, revisions, now + timedelta(hours=24))
    assert not fresh(report, revisions, now - timedelta(seconds=1))
    assert not fresh(report, dict(revisions, app="changed"), now)
    assert not fresh(report, {}, now)
    assert not fresh(dict(report, evidence_kind="direct_gateway_probe"), revisions, now)
    assert not fresh(dict(report, expires_at="invalid"), revisions, now)


def test_allow_requires_exact_marker_content(tmp_path: Path) -> None:
    with Fixture(tmp_path / "marker", 200, "allow") as fixture:
        fixture.tool_sent = fixture.tool_returned = True
        fixture.observations.append(
            {
                "path": "/api/v1/agents/permission-check",
                "input": {
                    "tool_name": "Bash",
                    "tool_input": {"command": f"printf fixture > {fixture.marker}"},
                },
            }
        )
        fixture.marker.write_text("unexpected")
        assert assess("allow", fixture, fixture.marker, 0)["result"] == "failed"


def test_bundle_hash_detects_content_and_path_drift(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    with pytest.raises(ValueError):
        bundle_digest(package)
    entry = package / "cli.js"
    entry.write_text("first")
    original = bundle_digest(package)
    entry.write_text("second")
    assert bundle_digest(package) != original
    entry.write_text("first")
    entry.rename(package / "different.js")
    assert bundle_digest(package) != original


def test_restoration_runs_both_profiles_and_clears_prior_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    import rig

    calls = []

    def fake_app(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        work = Path(kwargs["cwd"])
        settings = json.loads((work / "home/.claude/settings.json").read_text())
        calls.append((work, settings))
        marker = work / "marker"
        if len(calls) == 1:
            assert settings == {}
            marker.write_text("fixture")
        else:
            assert "hooks" in settings
            assert not marker.exists(), "old bypass marker must not contaminate deny"
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "FIXTURE_COMPLETE",
                }
            ),
            "",
        )

    monkeypatch.setattr(rig.subprocess, "run", fake_app)
    monkeypatch.setattr(
        rig,
        "assess",
        lambda name, *args: {
            "result": "boundary_gap" if name == "alternate_profile" else "passed",
        },
    )
    result = rig.run_case("restored_profile", tmp_path)
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert result["before_restoration"]["config_sha256"] != result["config_sha256"]
