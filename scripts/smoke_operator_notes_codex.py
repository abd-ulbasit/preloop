#!/usr/bin/env python3
"""Run installed Codex lifecycle hooks with a simulated Responses provider/API."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from smoke_operator_notes_claude import (
    START_MARKER,
    STOP_MARKER,
    FixtureHandler,
    FixtureServer,
)


class CodexFixtureHandler(FixtureHandler):
    """Serve Responses events, retaining the shared synthetic note API."""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
        if self.path.split("?", 1)[0] not in ("/v1/responses", "/responses"):
            super().do_POST()
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.server.model_requests.append(body)
        turn = len(self.server.model_requests)
        if turn == 1:
            self.server.stop_pending = True
        else:
            self.server.end_pending = True
        item: dict[str, Any] = {
            "id": f"msg_synthetic_{turn}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "text": f"Synthetic turn {turn} complete.",
                    "annotations": [],
                }
            ],
        }
        response = {
            "id": f"resp_synthetic_{turn}",
            "object": "response",
            "created_at": 1791849600,
            "status": "completed",
            "model": body.get("model", "gpt-5.4"),
            "output": [item],
            "usage": {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
        }
        events = [
            {
                "type": "response.created",
                "response": {**response, "output": [], "status": "in_progress"},
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**item, "status": "in_progress", "content": []},
            },
            {
                "type": "response.content_part.added",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "delta": item["content"][0]["text"],
            },
            {
                "type": "response.output_text.done",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "text": item["content"][0]["text"],
            },
            {
                "type": "response.content_part.done",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "part": item["content"][0],
            },
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        data = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    """Run the installed CLI without normal auth/config or a real provider."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preloop",
        required=True,
        type=Path,
        help="Preloop CLI binary containing Codex lifecycle hooks",
    )
    args = parser.parse_args()
    codex = shutil.which("codex")
    if not codex:
        parser.error("Missing prerequisite: installed codex executable")
    with tempfile.TemporaryDirectory(prefix="preloop-codex-note-smoke-") as directory:
        home = Path(directory)
        env = {
            **os.environ,
            "HOME": str(home),
            "CODEX_HOME": str(home),
            "PRELOOP_DISABLE_TELEMETRY": "true",
            "DO_NOT_TRACK": "1",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "PRELOOP_PARENT_SESSION_ID",
            "PRELOOP_SESSION_ALIAS",
        ):
            env.pop(name, None)
        server = FixtureServer()
        server.RequestHandlerClass = CodexFixtureHandler
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        env.update(
            {
                "PRELOOP_SMOKE_PROVIDER_KEY": "synthetic-local-only",
                "PRELOOP_FLOW_CREDENTIAL_REQUIRED": "1",
                "PRELOOP_FLOW_TOKEN": "agt_synthetic",
                "PRELOOP_FLOW_API_URL": base_url,
            }
        )
        (home / "config.toml").write_text(f'''model = "gpt-5.4"
model_provider = "fixture"
[model_providers.fixture]
name = "Loopback fixture"
base_url = "{base_url}/v1"
wire_api = "responses"
env_key = "PRELOOP_SMOKE_PROVIDER_KEY"
requires_openai_auth = false
''')
        settings: dict[str, Any] = {"hooks": {}}
        for event, command in (
            ("SessionStart", "session"),
            ("Stop", "stop"),
            ("SessionEnd", "session-end"),
        ):
            settings["hooks"][event] = [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{shlex.quote(str(args.preloop.resolve()))} agents permission-hook {command} --source codex_cli",
                        }
                    ],
                }
            ]
        (home / "hooks.json").write_text(json.dumps(settings))
        version = subprocess.run(
            [codex, "--version"], env=env, text=True, capture_output=True, check=True
        ).stdout.strip()
        try:
            result = subprocess.run(
                [
                    codex,
                    "exec",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--json",
                    "--sandbox",
                    "read-only",
                    "--dangerously-bypass-hook-trust",
                    "Reply with a short acknowledgement. Do not call tools.",
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
                    "evidence": "installed Codex; simulated provider and note API",
                    "version": version,
                    "model_requests": len(bodies),
                    "events": server.events,
                    "checks": checks,
                },
                indent=2,
            )
        )
        if not all(checks.values()):
            # Fixture process output contains no real credentials or account data.
            print(result.stderr[-3000:])
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
