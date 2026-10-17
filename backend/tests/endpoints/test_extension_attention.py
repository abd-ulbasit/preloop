"""Generic plugin inbox contributions are authorized before providers execute."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from preloop.api.endpoints.account import list_extension_attention_items
from preloop.plugins.attention import AttentionSource
from preloop.plugins.base import Plugin, PluginManager, PluginMetadata
from preloop.schemas.attention import ExtensionAttentionItem


def item(**changes: object) -> ExtensionAttentionItem:
    """Return a synthetic contribution."""
    return ExtensionAttentionItem.model_validate(
        dict(
            id="one",
            source="ignored",
            title="Review configuration",
            detail="Observed change",
            href="/console/settings/example",
            severity="action",
            opened_at=datetime.now(UTC),
        )
        | changes
    )


@pytest.mark.asyncio
async def test_no_sources_returns_empty_without_database_reads() -> None:
    manager = PluginManager()
    db = MagicMock()
    with patch("preloop.plugins.base.get_plugin_manager", return_value=manager):
        assert await list_extension_attention_items(SimpleNamespace(), db) == []
    db.assert_not_called()
    assert db.mock_calls == []


@pytest.mark.asyncio
async def test_permission_filter_namespace_dedupe_and_failure_isolation() -> None:
    user = SimpleNamespace(id=uuid4(), account_id=uuid4(), is_superuser=False)
    permitted = AsyncMock(return_value=[item(), item()])
    forbidden = AsyncMock(return_value=[item()])
    broken = AsyncMock(side_effect=RuntimeError("synthetic failure"))
    manager = MagicMock()
    manager.attention_sources.return_value = [
        ("example", AttentionSource("one", "view_example", permitted)),
        ("example", AttentionSource("private", "private", forbidden)),
        ("example", AttentionSource("broken", "view_example", broken)),
    ]
    with (
        patch("preloop.plugins.base.get_plugin_manager", return_value=manager),
        patch("preloop.api.endpoints.account.crud_account.get", return_value=None),
        patch(
            "preloop.api.auth.permissions.has_permission",
            side_effect=lambda u, p, db: p == "view_example",
        ),
    ):
        result = await list_extension_attention_items(user, MagicMock())
    assert len(result) == 1
    assert result[0].source == "example:one"
    permitted.assert_awaited_once()
    assert permitted.call_args.args[1] is user
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_credentials_cannot_read_sources() -> None:
    user = SimpleNamespace(
        _auth_api_key=SimpleNamespace(context_data={"device_report": {}})
    )
    with pytest.raises(HTTPException) as exc:
        await list_extension_attention_items(user, MagicMock())
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "href",
    ["https://example.com", "//example.com", "/console\\evil", "/console/\ninvalid"],
)
def test_destinations_must_be_local_console_paths(href: str) -> None:
    with pytest.raises(ValidationError):
        item(href=href)


def test_plugin_hook_defaults_and_manager_registration() -> None:
    class Example(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata("example", "1", "Example", "Example")

    manager = PluginManager()
    manager.register_plugin(Example())
    assert manager.attention_sources() == []
