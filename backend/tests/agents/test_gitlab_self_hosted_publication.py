"""Regression coverage for publication to self-hosted GitLab instances."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from preloop.agents.container import ContainerAgentExecutor
from tests.agents.test_capture_shell_nounset import _markers, _run


@pytest.mark.parametrize("scheme", ["http", "https", ""])
def test_project_bound_clone_preserves_instance_scheme(scheme: str) -> None:
    """A project binding without a raw URL uses the tracker's scheme and port."""
    executor = ContainerAgentExecutor("codex", {}, "test-image")
    project = MagicMock(slug="example/app", organization=MagicMock(tracker_id="t"))
    tracker = MagicMock(
        tracker_type="gitlab",
        url=f"{scheme + '://' if scheme else ''}gitlab.example.com:53080",
        resolved_api_key="test-token",
        auth_type="api_token",
    )
    with (
        patch(
            "preloop.models.db.session.get_db_session", return_value=iter([MagicMock()])
        ),
        patch("preloop.models.crud.crud_project.get", return_value=project),
        patch("preloop.models.crud.crud_tracker.get", return_value=tracker),
    ):
        assert (
            executor._resolve_repository_clone_url(
                {"project_id": "p"}, 0, {"account_id": "a"}, {}
            )
            == f"{scheme or 'https'}://gitlab.example.com:53080/example/app.git"
        )


def test_unsupported_publication_scheme_is_diagnosed() -> None:
    """A skipped SSH publication leaves a useful operator diagnostic."""
    executor = ContainerAgentExecutor("codex", {}, "test-image")
    with patch.object(executor.logger, "warning") as warning:
        assert (
            executor._build_pr_or_mr_create_shell(
                execution_context={},
                git_config={},
                token_ref="${TOKEN}",
                tracker_type="gitlab",
                host_kind=None,
                repo_url="ssh://git@gitlab.example.com/example/app.git",
                safe_target="preloop/issue-1",
                safe_source="main",
            )
            == ""
        )
    warning.assert_called_once()
    assert "unsupported" in warning.call_args.args[0]


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
