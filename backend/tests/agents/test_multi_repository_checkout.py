"""Exercise the shared checkout script against two real local repositories."""

import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from preloop.agents.codex import CodexAgent
from preloop.agents.container import ContainerAgentExecutor
from preloop.agents.gemini import GeminiAgent
from preloop.agents.opencode import OpenCodeAgent


@pytest.mark.parametrize(
    "executor_class", [ContainerAgentExecutor, CodexAgent, OpenCodeAgent, GeminiAgent]
)
def test_two_repositories_are_cloned_into_the_workspace(
    tmp_path: Path, executor_class: type[ContainerAgentExecutor]
) -> None:
    """Every container harness checks out both entries, with their own branches."""
    # Keep Git configuration and credentials entirely inside this test directory.
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Jane Doe",
        "GIT_AUTHOR_EMAIL": "jane@example.com",
        "GIT_COMMITTER_NAME": "Jane Doe",
        "GIT_COMMITTER_EMAIL": "jane@example.com",
        "PRELOOP_DISABLE_TELEMETRY": "true",
        "PRELOOP_GIT_CREDENTIALS": "",
    }

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    repositories: list[dict[str, str]] = []
    for name, branch in [("api", "main"), ("web", "release")]:
        source = tmp_path / f"source-{name}"
        git("init", "-b", branch, str(source))
        (source / "README.md").write_text(f"{name} repository\n")
        git("-C", str(source), "add", "README.md")
        git("-C", str(source), "commit", "-m", "Initial example")
        repositories.append(
            {
                "tracker_id": f"tracker-{name}",
                "repository_url": source.as_uri(),
                "clone_path": f"/workspace/{name}",
                "branch": branch,
            }
        )

    if executor_class is ContainerAgentExecutor:
        executor = executor_class(
            agent_type="codex", config={}, image="test-image", use_kubernetes=False
        )
    else:
        executor = executor_class(config={})
    context: dict[str, Any] = {
        "flow_id": "example-flow",
        "execution_id": "example-execution",
        "git_clone_config": {
            "enabled": True,
            "repositories": repositories,
            "target_branch": "agent/example",
        },
    }
    command = executor._prepare_git_clone_command(context)
    assert command.count("if ! git clone") == 2
    workspace = tmp_path / "workspace"
    # Map the container mount and optional checkpoint client into this test's sandbox.
    command = command.replace("/workspace", str(workspace)).replace(
        "/tmp/preloop-checkpoint-client.py", str(tmp_path / "no-checkpoint-client.py")
    )
    result = subprocess.run(
        ["bash", "-c", command], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("api", "web"):
        checkout = workspace / name
        assert (checkout / ".git").is_dir()
        assert (checkout / "README.md").read_text() == f"{name} repository\n"
        assert git("-C", str(checkout), "branch", "--show-current") == "agent/example"
        assert git("-C", str(checkout), "rev-parse", "HEAD") == git(
            "-C", str(tmp_path / f"source-{name}"), "rev-parse", "HEAD"
        )


@pytest.mark.parametrize(
    "executor_class", [ContainerAgentExecutor, CodexAgent, OpenCodeAgent, GeminiAgent]
)
def test_disabled_form_config_does_not_clone_even_with_a_trigger_project(
    executor_class: type[ContainerAgentExecutor],
) -> None:
    """The disabled form submits an empty list, bypassing explicit-repo cloning."""
    if executor_class is ContainerAgentExecutor:
        executor = executor_class(
            agent_type="codex", config={}, image="test-image", use_kubernetes=False
        )
    else:
        executor = executor_class(config={})
    context: dict[str, Any] = {
        "flow_id": "example-flow",
        "execution_id": "example-execution",
        "trigger_project_id": "example-trigger-project",
        "git_clone_config": {
            "enabled": False,
            "repositories": [],
            "repository_bindings": [
                {"tracker_id": "example-tracker", "repository": "team/api"}
            ],
        },
    }
    with patch.object(executor, "_prepare_git_clone_command") as clone:
        command = executor._prepare_init_commands(context)
    clone.assert_not_called()
    assert command == ""
