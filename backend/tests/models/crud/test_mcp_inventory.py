"""Account isolation and durable metadata-only MCP evidence storage."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_account, crud_mcp_inventory, crud_mcp_server


def test_owned_revision_required_and_evidence_is_deduplicated(
    db_session: Session, test_user: models.User
) -> None:
    server = crud_mcp_server.create(
        db_session,
        obj_in={
            "account_id": test_user.account_id,
            "name": "synthetic",
            "url": "http://example.com/mcp",
        },
    )
    other = crud_account.create(
        db_session, obj_in={"organization_name": "Synthetic other"}
    )
    revision = server.updated_at.isoformat()
    now = datetime.now(UTC)
    kwargs = dict(
        server_id=server.id, revision=revision, evidence_class="observed_call", at=now
    )
    with pytest.raises(ValueError, match="not owned"):
        crud_mcp_inventory.observe(db_session, account_id=other.id, **kwargs)
    with pytest.raises(ValueError, match="not owned"):
        crud_mcp_inventory.declare(
            db_session,
            account_id=other.id,
            server_id=server.id,
            revision=revision,
            state="approved",
        )
    with pytest.raises(ValueError, match="not owned"):
        crud_mcp_inventory.observe(
            db_session,
            account_id=test_user.account_id,
            **{**kwargs, "revision": "stale"},
        )
    first = crud_mcp_inventory.observe(
        db_session, account_id=test_user.account_id, **kwargs
    )
    again = crud_mcp_inventory.observe(
        db_session,
        account_id=test_user.account_id,
        **{**kwargs, "at": now + timedelta(seconds=1)},
    )
    assert first.id == again.id and first.first_seen_at == now
    assert first.last_seen_at == now + timedelta(seconds=1)
    configured = crud_mcp_inventory.observe(
        db_session,
        account_id=test_user.account_id,
        **{**kwargs, "evidence_class": "configured_route"},
    )
    assert configured.id != first.id
    assert (
        crud_mcp_inventory.get(db_session, account_id=other.id, finding_id=first.id)
        is None
    )
    crud_mcp_inventory.declare(
        db_session,
        account_id=test_user.account_id,
        server_id=server.id,
        revision=revision,
        state="unapproved",
    )
    review = crud_mcp_inventory.review(
        db_session,
        account_id=test_user.account_id,
        server_id=server.id,
        revision=revision,
    )
    assert review.approval_state == "unapproved"
    assert server.status == "active"
    assert "url" not in models.MCPInventoryFinding.__table__.columns
    assert "arguments" not in models.MCPInventoryFinding.__table__.columns
