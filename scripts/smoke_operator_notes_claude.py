#!/usr/bin/env python3
"""Run installed Claude with isolated hooks and a simulated local provider/API."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

START_MARKER = "PRELOOP_SYNTHETIC_START_MARKER"
STOP_MARKER = "PRELOOP_SYNTHETIC_STOP_MARKER"
END_MARKER = "PRELOOP_SYNTHETIC_END_MARKER"
RUNTIME_ID = "11111111-1111-4111-8111-111111111111"


class FixtureServer(ThreadingHTTPServer):
    """Keep synthetic lifecycle evidence; never contact an upstream provider."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.model_requests: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.ended = False
        self.terminal_state = "pending"
        self.start_pending = True
        self.stop_pending = False
        self.end_pending = False


class FixtureHandler(BaseHTTPRequestHandler):
    server: FixtureServer

    def log_message(self, format: str, *args: Any) -> None:
        """Keep request bodies and headers out of console output."""

    def send_json(self, data: dict[str, Any]) -> None:
        """Write one synthetic response."""
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
        """Serve only local fixture endpoints."""
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        path = self.path.split("?", 1)[0]
        if path == "/api/v1/agents/session-start":
            self.send_json(
                {"runtime_session_id": RUNTIME_ID, "started_at": "2026-10-13T00:00:00Z"}
            )
        elif path == "/api/v1/agents/notes/pending":
            event = body.get("event", "")
            self.server.events.append(event)
            marker = None
            if event == "session_start" and self.server.start_pending:
                self.server.start_pending = False
                marker = START_MARKER
            elif event == "stop" and self.server.stop_pending:
                self.server.stop_pending = False
                marker = STOP_MARKER
            self.send_json(
                {
                    "text": f"<operator-notes>{marker}</operator-notes>"
                    if marker
                    else None,
                    "notes": [{"note_id": marker}] if marker else [],
                    "channel_event": None,
                }
            )
        elif path == "/api/v1/agents/notes/ack":
            self.send_json({"acknowledged": 1, "marked": 1})
        elif path == "/api/v1/agents/session-end":
            self.server.ended = True
            if self.server.end_pending:
                self.server.terminal_state = "undeliverable"
            self.send_json({"marked": 1})
        elif path == "/v1/messages/count_tokens":
            self.send_json({"input_tokens": 100})
        elif path == "/v1/messages":
            self.server.model_requests.append(body)
            turn = len(self.server.model_requests)
            if turn == 1:
                self.server.stop_pending = True
            else:
                self.server.end_pending = True
            self.respond_model(body, turn)
        else:
            self.send_error(404)

    def respond_model(self, body: dict[str, Any], turn: int) -> None:
        """Return a minimal Anthropic text turn, with SSE when requested."""
        message = {
            "id": f"msg_synthetic_{turn}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "claude-haiku-4-5"),
            "content": [{"type": "text", "text": f"Synthetic turn {turn} complete."}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 5},
        }
        if not body.get("stream"):
            self.send_json(message)
            return
        events = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {**message, "content": [], "stop_reason": None},
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": message["content"][0]["text"],
                    },
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 5},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        data = "".join(
            f"event: {name}\ndata: {json.dumps(event)}\n\n" for name, event in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    """Exercise actual harness parsers with no credential/config reuse."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preloop",
        type=Path,
        help="CLI binary; otherwise build in temporary directory",
    )
    args = parser.parse_args()
    claude = shutil.which("claude")
    if not claude:
        parser.error("Missing prerequisite: installed claude executable")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="preloop-claude-note-smoke-") as directory:
        home = Path(directory)
        config = home / ".claude"
        config.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(config),
            "PRELOOP_DISABLE_TELEMETRY": "true",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "ANTHROPIC_API_KEY": "sk-ant-synthetic-local-only",
        }
        # Clear inherited provider modes and hooks; only this local provider is used.
        for name in (
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "PRELOOP_PARENT_SESSION_ID",
            "PRELOOP_SESSION_ALIAS",
        ):
            env.pop(name, None)
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
        binary = args.preloop.resolve() if args.preloop else home / "preloop"
        if not args.preloop:
            subprocess.run(
                ["go", "build", "-o", str(binary), "./cmd/preloop"],
                cwd=root / "cli",
                check=True,
                env={**os.environ, "PRELOOP_DISABLE_TELEMETRY": "true"},
            )
        server = FixtureServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        env.update(
            {
                "ANTHROPIC_BASE_URL": base_url,
                "PRELOOP_FLOW_CREDENTIAL_REQUIRED": "1",
                "PRELOOP_FLOW_TOKEN": "agt_synthetic",
                "PRELOOP_FLOW_API_URL": base_url,
            }
        )
        settings = {"hooks": {}}
        for event, command in (
            ("SessionStart", "session"),
            ("Stop", "stop"),
            ("SessionEnd", "session-end"),
        ):
            settings["hooks"][event] = [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{shlex.quote(str(binary))} agents permission-hook {command} --source claude_code",
                        }
                    ],
                }
            ]
        settings_file = home / "hooks.json"
        settings_file.write_text(json.dumps(settings))
        version = subprocess.run(
            [claude, "--version"], env=env, text=True, capture_output=True, check=True
        ).stdout.strip()
        try:
            result = subprocess.run(
                [
                    claude,
                    "-p",
                    "Reply with a short acknowledgement. Do not call tools.",
                    "--model",
                    "haiku",
                    "--tools",
                    "",
                    "--setting-sources",
                    "",
                    "--settings",
                    str(settings_file),
                    "--strict-mcp-config",
                    "--mcp-config",
                    '{"mcpServers":{}}',
                    "--no-session-persistence",
                ],
                cwd=home,
                env=env,
                text=True,
                capture_output=True,
                timeout=90,
            )
        finally:
            server.shutdown()
            server.server_close()
        bodies = [json.dumps(request) for request in server.model_requests]
        checks = {
            "process_exit_zero": result.returncode == 0,
            "session_start_consumed_by_model_request": bool(bodies)
            and START_MARKER in bodies[0],
            "stop_note_consumed_by_next_model_request": len(bodies) >= 2
            and STOP_MARKER in bodies[1],
            "session_end_emitted": server.ended,
            "remaining_note_undeliverable_in_fixture_api": server.terminal_state
            == "undeliverable",
        }
        print(
            json.dumps(
                {
                    "evidence": "installed Claude; simulated provider and note API",
                    "version": version,
                    "model_requests": len(bodies),
                    "events": server.events,
                    "checks": checks,
                },
                indent=2,
            )
        )
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
