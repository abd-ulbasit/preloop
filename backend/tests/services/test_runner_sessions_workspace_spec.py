"""The stored remote-session workspace spec keeps the #1484 fields only."""

from preloop.services.runner_sessions import sanitize_workspace


def test_sanitize_workspace_keeps_provider_and_path_drops_credential() -> None:
    stored = sanitize_workspace(
        {
            "kind": "tracker_checkout",
            "tracker_id": "t1",
            "provider": "github",
            "repository": "acme/app",
            "ref": "main",
            "credential": {"username": "x-access-token", "token": "ghs_secret"},
        }
    )
    assert stored == {
        "kind": "tracker_checkout",
        "tracker_id": "t1",
        "provider": "github",
        "repository": "acme/app",
        "ref": "main",
    }


def test_sanitize_workspace_allow_dirty_only_when_true() -> None:
    base = {"kind": "authorized_directory", "id": "dir_9f2c", "path": "pkg/api"}
    assert sanitize_workspace({**base, "allow_dirty": "yes"}) == base
    assert sanitize_workspace({**base, "allow_dirty": True}) == {
        **base,
        "allow_dirty": True,
    }
