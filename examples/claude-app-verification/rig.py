"""Local cooperative hook evidence, never fleet or managed-control attestation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

APP_VERSION = "2.1.296"
FIXTURE_VERSION = "cooperative-hook-v1"
CASES = {
    "allow": (200, "allow", False, True),
    "deny": (200, "deny", False, False),
    "deny_fail_open": (200, "deny", True, False),
    "503_closed": (503, "deny", False, False),
    "503_open": (503, "deny", True, True),
    "transport_closed": (0, "deny", False, False),
    "transport_open": (0, "deny", True, True),
    "credential_401": (401, "deny", True, False),
    "alternate_profile": (200, "allow", False, True),
    "restored_profile": (200, "deny", False, False),
}
SKIPPED = {
    "approval_allow": "No real backend approval workflow; allow only tests terminal hook response.",
    "approval_deny": "No real backend approval workflow; deny only tests terminal hook response.",
    "approvals_off": "Requires actual backend inherited policy, absent in synthetic service.",
    "no_matching_rule": "Requires actual backend policy evaluation, absent in synthetic service.",
    "revoked_credential": "401 fixture tests hook behavior, not actual credential lifecycle revocation.",
    "model_restriction": "Local model stub has no production model policy enforcement.",
    "budget_restriction": "Local model stub has no production budget accounting.",
    "direct_mcp": "Native Bash fixture does not exercise a direct MCP server.",
    "oauth_rollback": "No OAuth credentials present; latest-lineage restoration is unverified.",
    "managed_settings": "Cooperative user settings only; no MDM or Desktop verification.",
}


def digest(data: bytes) -> str:
    """Hash non-secret fixture inputs or binaries."""
    return hashlib.sha256(data).hexdigest()


def fresh(report: dict[str, Any], revisions: dict[str, str], now: datetime) -> bool:
    """Reject stale or incomplete evidence; 24 hours is a fixture-only lifetime."""
    try:
        start = datetime.fromisoformat(report["verified_at"])
        end = datetime.fromisoformat(report["expires_at"])
        return (
            report["schema_version"] == 1
            and report["evidence_kind"] == "app_harness"
            and set(revisions)
            == {"app", "cli", "config", "policy", "credential", "rig"}
            and all(revisions.values())
            and report["revisions"] == revisions
            and start <= now < end
            and end - start <= timedelta(hours=24)
        )
    except (KeyError, TypeError, ValueError):
        return False


def events(content: dict[str, Any], model: str) -> list[tuple[str, dict[str, Any]]]:
    """Emit a complete Anthropic streamed tool or terminal text turn."""
    tool = content["type"] == "tool_use"
    initial = dict(content, input={}) if tool else dict(content, text="")
    delta = (
        {"type": "input_json_delta", "partial_json": json.dumps(content["input"])}
        if tool
        else {"type": "text_delta", "text": content["text"]}
    )
    return [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": initial},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": delta},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "tool_use" if tool else "end_turn",
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 1},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]


class Fixture(ThreadingHTTPServer):
    """Loopback fixture with a per-case transcript, no upstream forwarding."""

    def __init__(self, marker: Path, status: int, decision: str) -> None:
        self.marker = marker
        self.status = status
        self.decision = decision
        self.observations: list[dict[str, Any]] = []
        self.tool_sent = False
        self.tool_returned = False
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(BaseHTTPRequestHandler):
    """Serve only the synthetic messages and permission contracts."""

    server: Fixture

    def log_message(self, fmt: str, *args: Any) -> None:
        """Do not print request or credential headers."""

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/v1/agents/permission-check":
            self.server.observations.append(
                {"path": self.path, "input": payload, "status": self.server.status}
            )
            if self.server.status == 0:
                self.connection.shutdown(2)
                self.connection.close()
                return
            self.send_json(
                self.server.status,
                {
                    "decision": self.server.decision,
                    "reason": "synthetic fixture",
                    "request_id": "fixture",
                },
            )
            return
        if "count_tokens" in self.path:
            self.send_json(200, {"input_tokens": 1})
            return
        if self.path.split("?")[0] != "/v1/messages":
            self.send_json(404, {"error": "unsupported fixture endpoint"})
            return
        returned = [
            block
            for message in payload.get("messages", [])
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
            and block.get("tool_use_id") == "tool_fixture"
        ]
        self.server.tool_returned |= bool(returned)
        self.server.observations.append(
            {"path": "/v1/messages", "tool_results": returned}
        )
        if self.server.tool_sent:
            content = {"type": "text", "text": "FIXTURE_COMPLETE"}
        else:
            self.server.tool_sent = True
            content = {
                "type": "tool_use",
                "id": "tool_fixture",
                "name": "Bash",
                "input": {
                    "command": f"printf fixture > {self.server.marker}",
                    "description": "Write synthetic marker",
                },
            }
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for name, value in events(content, payload["model"]):
            self.wfile.write(f"event: {name}\ndata: {json.dumps(value)}\n\n".encode())
        self.wfile.flush()
        self.close_connection = True


def assess(
    name: str, fixture: Fixture, marker: Path, returncode: int
) -> dict[str, Any]:
    """A missing effect alone cannot pass a denied probe."""
    permission = [
        item
        for item in fixture.observations
        if item["path"].endswith("permission-check")
    ]
    correlated = any(
        item["input"].get("tool_name") == "Bash"
        and item["input"].get("tool_input", {}).get("command")
        == f"printf fixture > {marker}"
        for item in permission
    )
    complete = returncode == 0 and fixture.tool_sent and fixture.tool_returned
    boundary = name == "alternate_profile"
    correct = marker.exists() == CASES[name][3]
    if marker.exists():
        correct = correct and marker.is_file() and marker.read_text() == "fixture"
    passed = complete and correct and (not permission if boundary else correlated)
    return {
        "case": name,
        "result": ("boundary_gap" if boundary else "passed") if passed else "failed",
        "marker_present": marker.exists(),
        "app_exit_code": returncode,
        "tool_requested": fixture.tool_sent,
        "tool_result_received": fixture.tool_returned,
        "permission_correlated": correlated,
        "tested_path": "actual_claude_code_to_cli_hook_to_synthetic_permission_service",
        "observations": fixture.observations,
    }


def run_case(name: str, root: Path) -> dict[str, Any]:
    """Run the real app in a fresh synthetic profile against loopback fixtures."""
    work = root / name
    work.mkdir()
    home = work / "home"
    home.mkdir()
    profile = home / ".claude"
    profile.mkdir()
    marker = work / "marker"
    status, decision, fail_open, _ = CASES[name]
    server = Fixture(marker, status, decision)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    credentials = home / ".preloop" / "agents" / "fixture"
    credentials.mkdir(parents=True)
    (credentials / "permission_hook.json").write_text(
        json.dumps(
            {
                "base_url": base,
                "token": "synthetic-runtime-token",
                "source": "claude_code",
            }
        )
    )
    hook = "preloop agents permission-hook --source claude_code"
    if fail_open:
        hook += " --fail-open"
    settings: dict[str, Any] = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "*", "hooks": [{"type": "command", "command": hook}]}
            ]
        }
    }
    original = json.dumps(settings)
    settings_path = profile / "settings.json"
    settings_path.write_text(original)
    if name in ("alternate_profile", "restored_profile"):
        settings_path.write_text("{}")
    if name == "restored_profile":
        settings_path.write_text(original)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(profile),
        "TMPDIR": str(work),
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_API_KEY": "synthetic-fixture-key",
        "PRELOOP_DISABLE_TELEMETRY": "true",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    command = [
        "claude",
        "-p",
        "Execute the fixture tool once.",
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "--max-turns",
        "3",
        "--model",
        "claude-sonnet-4-5",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
    ]
    try:
        result = subprocess.run(
            command,
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        record = assess(name, server, marker, result.returncode)
        try:
            terminal = json.loads(result.stdout)
            terminal_ok = (
                terminal.get("type") == "result"
                and terminal.get("subtype") == "success"
                and terminal.get("is_error") is False
                and terminal.get("result") == "FIXTURE_COMPLETE"
            )
        except (ValueError, AttributeError):
            terminal_ok = False
        record["terminal_app_result"] = terminal_ok
        if not terminal_ok:
            record["result"] = "failed"
        record.update(
            {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "command": command,
                "config_sha256": digest(settings_path.read_bytes()),
            }
        )
        return record
    except subprocess.TimeoutExpired:
        return {
            "case": name,
            "result": "failed",
            "reason": "actual app timed out",
            "observations": server.observations,
            "marker_present": marker.exists(),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main() -> int:
    """Require an explicit isolated-container opt-in before starting the app."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-app", action="store_true", required=True)
    parser.parse_args()
    if not Path("/.dockerenv").exists():
        raise SystemExit("Use run.sh: host execution is prohibited")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="app-verification-") as directory:
        root = Path(directory)
        env = {
            "HOME": directory,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PRELOOP_DISABLE_TELEMETRY": "true",
        }
        version = subprocess.check_output(
            ["claude", "--version"], env=env, cwd=root, text=True
        ).strip()
        if version != f"{APP_VERSION} (Claude Code)":
            raise SystemExit(f"unsupported app version: {version}")
        now = datetime.now(timezone.utc)
        report = {
            "schema_version": 1,
            "evidence_kind": "app_harness",
            "scope": "local_cooperative_hook_only",
            "app_version": version,
            "verified_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=24)).isoformat(),
            "revisions": {
                "app": APP_VERSION,
                "cli": digest(Path("/usr/local/bin/preloop").read_bytes()),
                "config": digest(json.dumps(CASES, sort_keys=True).encode()),
                "policy": FIXTURE_VERSION,
                "credential": "synthetic-v1",
                "rig": digest(Path(__file__).read_bytes()),
            },
            "results": [run_case(name, root) for name in CASES]
            + [
                {"case": name, "result": "skipped", "reason": reason}
                for name, reason in SKIPPED.items()
            ],
        }
        Path("/evidence/report.json").write_text(json.dumps(report, indent=2) + "\n")
        return int(any(item["result"] == "failed" for item in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
