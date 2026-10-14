"""Exact-resource view denials apply equally to owner detail-by-ID routes."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.plugins import account_hooks


def _resource(kind: str, user: Any) -> Any:
    now = datetime.now(timezone.utc)
    values = {
        "ai_model": lambda: models.AIModel(
            account_id=user.account_id,
            name="Synthetic",
            provider_name="example",
            model_identifier="example",
        ),
        "mcp_server": lambda: models.MCPServer(
            account_id=user.account_id,
            name="Synthetic",
            url="https://example.test",
        ),
        "managed_agent": lambda: models.ManagedAgent(
            account_id=user.account_id,
            agent_kind="codex",
            session_source_type="test",
            session_source_id=str(uuid4()),
            display_name="Synthetic",
            lifecycle_updated_at=now,
            last_seen_at=now,
        ),
        "flow": lambda: models.Flow(
            account_id=user.account_id,
            name="Synthetic",
            prompt_template="test",
            agent_config={},
        ),
        "runner": lambda: models.FlowRunner(
            account_id=user.account_id,
            registered_by_user_id=user.id,
            name="Synthetic",
            token_hash="synthetic",
        ),
    }
    return values[kind]()


@pytest.mark.parametrize(
    "kind,path,suffix",
    [
        ("ai_model", "/ai-models", ""),
        ("ai_model", "/ai-models", "/summary"),
        ("ai_model", "/ai-models", "/runtime-sessions"),
        ("ai_model", "/ai-models", "/interactions"),
        ("ai_model", "/ai-models", "/pricing"),
        ("mcp_server", "/mcp-servers", ""),
        ("mcp_server", "/mcp-servers", "/tools"),
        ("managed_agent", "/agents", ""),
        ("flow", "/flows", ""),
        ("flow", "/flows", "/gateway-usage/summary"),
        ("runner", "/runners", ""),
    ],
)
def test_owner_detail_checks_resource_view_before_projection(
    client: TestClient,
    db_session: Session,
    test_user: Any,
    kind: str,
    path: str,
    suffix: str,
) -> None:
    row = _resource(kind, test_user)
    db_session.add(row)
    db_session.flush()
    calls = []
    previous = account_hooks.get_authorizer()

    def deny(ctx: Any, action: str, resource: Any = None) -> Any:
        if action == account_hooks.ACTION_RESOURCE_VIEW:
            identity = resource.get("id") if isinstance(resource, dict) else resource.id
            calls.append((ctx.attributes["resource_type"], str(identity)))
            return account_hooks.Decision("deny", reason="Exact-resource restriction")
        return account_hooks.Decision("allow")

    account_hooks.register_authorizer(deny)
    try:
        response = client.get(f"/api/v1{path}/{row.id}{suffix}")
        assert response.status_code == 404, response.text
        assert calls == [(kind, str(row.id))]
    finally:
        account_hooks.register_authorizer(previous)


@pytest.mark.parametrize(
    "kind,path,key",
    [
        ("ai_model", "/ai-models/overview", "models"),
        ("managed_agent", "/agents/control", "items"),
    ],
)
def test_alternate_inventory_lists_filter_exact_resource(
    client: TestClient,
    db_session: Session,
    test_user: Any,
    kind: str,
    path: str,
    key: str,
) -> None:
    row = _resource(kind, test_user)
    db_session.add(row)
    db_session.flush()
    calls = []
    previous = account_hooks.get_authorizer()

    def deny(ctx: Any, action: str, resource: Any = None) -> Any:
        if action == account_hooks.ACTION_RESOURCE_VIEW:
            identity = resource.get("id") if isinstance(resource, dict) else resource.id
            calls.append((ctx.attributes["resource_type"], str(identity)))
            return account_hooks.Decision("deny")
        return account_hooks.Decision("allow")

    account_hooks.register_authorizer(deny)
    try:
        response = client.get(f"/api/v1{path}")
        assert response.status_code == 200, response.text
        assert response.json()[key] == []
        assert calls == [(kind, str(row.id))]
    finally:
        account_hooks.register_authorizer(previous)
