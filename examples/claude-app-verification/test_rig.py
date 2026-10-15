"""Fixture-only tests. These cannot establish app verification."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from rig import CASES, Fixture, assess, events, fresh


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
