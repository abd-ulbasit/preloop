"""Exact-resource view denials apply equally to owner detail-by-ID routes."""

from datetime import datetime, timezone
from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock
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


@pytest.fixture(autouse=True)
def external_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    from preloop.api.endpoints import ai_models, mcp_servers
    from preloop.services.secret_service import (
        PRINCIPAL_BOUND_OAUTH_CREDENTIAL_TYPES,
        ResolvedModelCredentials,
    )

    monkeypatch.setattr(
        mcp_servers, "get_cached_tools_for_server", AsyncMock(return_value=[])
    )
    resolved = ResolvedModelCredentials(
        credential_type=next(iter(PRINCIPAL_BOUND_OAUTH_CREDENTIAL_TYPES)),
        backend_type="test",
        payload={"access": "synthetic", "expires": 2000000000000},
    )

    def credentials(*args: Any, **kwargs: Any) -> ResolvedModelCredentials:
        return resolved

    service = SimpleNamespace(resolve_ai_model_credentials=credentials)
    monkeypatch.setattr(ai_models, "get_secret_service", lambda: service)


@pytest.mark.parametrize("decision", ["deny", "allow"])
@pytest.mark.parametrize(
    "kind,path,suffix",
    [
        ("ai_model", "/ai-models", ""),
        ("ai_model", "/ai-models", "/summary"),
        ("ai_model", "/ai-models", "/runtime-sessions"),
        ("ai_model", "/ai-models", "/interactions"),
        ("ai_model", "/ai-models", "/pricing"),
        ("ai_model", "/ai-models", "/credentials/marker"),
        ("mcp_server", "/mcp-servers", ""),
        ("mcp_server", "/mcp-servers", "/tools"),
        ("managed_agent", "/agents", ""),
        ("managed_agent", "/agents", "/shared-sessions"),
        ("managed_agent", "/agents", "/model-bindings"),
        ("managed_agent", "/agents", "/governance"),
        ("managed_agent", "/agents", "/credentials"),
        ("managed_agent", "/agents", "/enrollments"),
        ("flow", "/flows", ""),
        ("flow", "/flows", "/gateway-usage/summary"),
        ("flow", "/account/governance/flows", ""),
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
    decision: str,
) -> None:
    row = _resource(kind, test_user)
    db_session.add(row)
    db_session.flush()
    calls = []
    previous = account_hooks.get_authorizer()
    account_hooks.register_authorizer(None)
    path = f"/api/v1{path}/{row.id}{suffix}?start_date=2026-10-01T00:00:00Z&end_date=2026-10-02T00:00:00Z"
    baseline = client.get(path) if decision == "allow" else None

    def deny(ctx: Any, action: str, resource: Any = None) -> Any:
        if action == account_hooks.ACTION_RESOURCE_VIEW:
            identity = resource.get("id") if isinstance(resource, dict) else resource.id
            calls.append((ctx.attributes["resource_type"], str(identity)))
            return account_hooks.Decision(decision, reason="Exact-resource restriction")
        return account_hooks.Decision("allow")

    account_hooks.register_authorizer(deny)
    try:
        response = client.get(path)
        if decision == "deny":
            assert response.status_code == 404, response.text
        else:
            assert baseline.status_code == response.status_code == 200, response.text
            assert response.json() == baseline.json()
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


def test_agent_list_and_detail_share_summary_attributes(
    client: TestClient,
    db_session: Session,
    test_user: Any,
) -> None:
    agent = _resource("managed_agent", test_user)
    agent.tags = {"env": "prod"}
    agent.owner_user_id = test_user.id
    db_session.add(agent)
    db_session.flush()
    seen = []
    previous = account_hooks.get_authorizer()

    def by_attributes(ctx: Any, action: str, resource: Any = None) -> Any:
        if action != account_hooks.ACTION_RESOURCE_VIEW:
            return account_hooks.Decision("allow")
        seen.append(
            {key: resource[key] for key in ("display_name", "tags", "owner_username")}
        )
        return account_hooks.Decision(
            "deny" if resource["display_name"] == "Synthetic" else "allow"
        )

    account_hooks.register_authorizer(by_attributes)
    try:
        listing = client.get("/api/v1/agents")
        assert listing.status_code == 200 and listing.json()["items"] == []
        assert client.get(f"/api/v1/agents/{agent.id}").status_code == 404
        assert (
            seen
            == [
                {
                    "display_name": "Synthetic",
                    "tags": {"env": "prod"},
                    "owner_username": test_user.username,
                }
            ]
            * 2
        )
    finally:
        account_hooks.register_authorizer(previous)


@pytest.mark.parametrize("decision", ["deny", "allow"])
def test_credential_export_is_a_resource_read(
    client: TestClient,
    db_session: Session,
    test_user: Any,
    decision: str,
) -> None:
    model = _resource("ai_model", test_user)
    db_session.add(model)
    db_session.flush()
    previous = account_hooks.get_authorizer()
    account_hooks.register_authorizer(
        lambda ctx, action, resource: account_hooks.Decision(
            decision if action == account_hooks.ACTION_RESOURCE_VIEW else "allow"
        )
    )
    try:
        response = client.post(f"/api/v1/ai-models/{model.id}/credentials/export")
        assert response.status_code == (404 if decision == "deny" else 200), (
            response.text
        )
        if decision == "allow":
            assert response.json()["access"] == "synthetic"
    finally:
        account_hooks.register_authorizer(previous)
