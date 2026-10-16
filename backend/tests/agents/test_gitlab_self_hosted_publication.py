"""Regression coverage for publication to self-hosted GitLab instances."""

from pathlib import Path

import pytest

from preloop.agents.container import ContainerAgentExecutor
from tests.agents.test_capture_shell_nounset import _markers, _run


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("existing", [False, True])
def test_gitlab_publication_preserves_instance_url(
    tmp_path: Path, scheme: str, existing: bool
) -> None:
    """Create, lookup and capture use the instance's scheme and custom port."""
    base = f"{scheme}://host.docker.internal:53080"
    executor = ContainerAgentExecutor("codex", {}, "test-image")
    script = executor._build_pr_or_mr_create_shell(
        execution_context={},
        git_config={},
        token_ref="${TOKEN}",
        tracker_type="gitlab",
        host_kind=None,
        repo_url=f"{base}/example/subgroup/app.git",
        safe_target="preloop/issue-1",
        safe_source="main",
    )
    api_url = f"{base}/api/v4/projects/example%2Fsubgroup%2Fapp/merge_requests"
    assert api_url in script
    assert f"{api_url}?state=opened&source_branch=preloop/issue-1" in script
    assert f"{api_url}/" in script  # Existing MR provenance update.
    if scheme == "http":
        assert "https://host.docker.internal" not in script
    # Run only the capture block, with the same fake REST responses as the
    # strict-shell regression suite. An HTTP URL must survive extraction too.
    capture = script[script.index("    # Initialised here") :]
    url = f"{base}/example/subgroup/app/-/merge_requests/5"
    response = '{"iid":5,"web_url":"' + url + '"}'
    completed = _run(
        tmp_path,
        capture,
        response='{"message":"already exists"}' if existing else response,
        lookup="[" + response + "]",
        flags=("-euo", "pipefail"),
    )
    assert completed.returncode == 0, completed.stderr
    assert _markers(completed.stdout) == [
        {"url": url, "branch": "preloop/issue-1", "provider": "gitlab"}
    ]
