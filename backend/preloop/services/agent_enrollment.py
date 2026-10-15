"""Single-use app provisioning, independent from device discovery reporting."""

from datetime import UTC, datetime, timedelta
import hashlib
import secrets
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from preloop.api.auth.key_scopes import ENROLLMENT_PROVISION_SCOPE
from preloop.models import models
from preloop.models.crud import (
    crud_api_key,
    crud_audit_log,
    crud_managed_agent,
    crud_managed_agent_credential,
    crud_managed_agent_enrollment,
)
from preloop.models.crud.billing import billing
from preloop.models.crud.capacity import authorize_capacity
from preloop.schemas.agent_enrollment import (
    EnrollmentAppCredential,
    EnrollmentExchangeRequest,
    EnrollmentExchangeResponse,
    EnrollmentGrantRequest,
)


def _audit(db: Session, key: models.ApiKey, action: str) -> None:
    """Record safe credential identity in the caller-owned transaction."""
    crud_audit_log.create(
        db,
        obj_in={
            "account_id": key.account_id,
            "user_id": key.user_id,
            "action": action,
            "resource_type": "api_key",
            "resource_id": str(key.id),
            "status": "success",
        },
        commit=False,
    )


def issue_authority(
    db: Session, *, user: models.User, grant: EnrollmentGrantRequest
) -> tuple[models.ApiKey, str]:
    """Issue hashed authority; caller commits with administrative audit."""
    key, token = crud_api_key.create_runtime_key(
        db,
        name="App enrollment authority",
        account_id=user.account_id,
        user_id=user.id,
        scopes=[ENROLLMENT_PROVISION_SCOPE],
        key_value=f"enr_{secrets.token_urlsafe(32)}",
        expires_at=(
            datetime.now(UTC) + timedelta(minutes=grant.expires_minutes)
        ).replace(tzinfo=None),
        context_data={"app_enrollment_grant": grant.model_dump()},
        commit=False,
    )
    _audit(db, key, "app_enrollment_authority_issued")
    return key, token


def exchange_authority(
    db: Session, *, authenticated_key: models.ApiKey, payload: EnrollmentExchangeRequest
) -> EnrollmentExchangeResponse:
    """Consume authority and create every selected app in one transaction.

    Row reload under lock checks revocation and expiry again after authentication.
    Account serialization also prevents distinct grants duplicating a target.
    Existing identities require explicit operator recovery, never self-reissue.
    """
    billing.lock_account(db, str(authenticated_key.account_id))
    key = crud_api_key.get_for_update(
        db, id=authenticated_key.id, account_id=authenticated_key.account_id
    )
    if key is None or not key.is_valid() or key.scopes != [ENROLLMENT_PROVISION_SCOPE]:
        raise HTTPException(403, "Enrollment authority is invalid or consumed")
    context = key.context_data or {}
    try:
        grant = EnrollmentGrantRequest.model_validate(context["app_enrollment_grant"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(403, "Invalid enrollment grant") from None
    if set(payload.apps) != set(grant.apps):
        raise HTTPException(403, "Selected apps do not match the enrollment grant")
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=grant.credential_hours)
    credentials = []
    for app in sorted(payload.apps):
        source_type = "desktop_agent" if app == "cursor" else app
        identity = hashlib.sha256(
            f"{payload.workstation_fingerprint}:{payload.user_ref}:{app}".encode()
        ).hexdigest()
        existing = crud_managed_agent.get_by_source(
            db,
            account_id=str(key.account_id),
            session_source_type=source_type,
            session_source_id=identity,
        )
        if existing is not None:
            raise HTTPException(
                409, "Target already enrolled; operator recovery required"
            )
        authorize_capacity(db, str(key.account_id), "agents")
        agent = crud_managed_agent.create(
            db,
            obj_in={
                "account_id": key.account_id,
                "owner_user_id": key.user_id,
                "agent_kind": app,
                "session_source_type": source_type,
                "session_source_id": identity,
                "display_name": f"Managed {app}",
                "enrolled_via": "unattended",
                "managed_mcp_servers": [],
                "tags": {},
                "lifecycle_state": "active",
                "lifecycle_updated_at": now,
                "last_seen_at": now,
            },
            commit=False,
        )
        binding: dict[str, Any] = payload.model_dump(
            exclude={"apps"}, exclude_none=True
        )
        binding["app"] = app
        api_key, token = crud_api_key.create_runtime_key(
            db,
            name=f"Managed app {agent.id}",
            account_id=key.account_id,
            user_id=key.user_id,
            scopes=["mcp:read", "mcp:write"],
            expires_at=expires_at.replace(tzinfo=None),
            key_value=f"agt_{secrets.token_urlsafe(32)}",
            context_data={
                "managed_agent_id": str(agent.id),
                "credential_kind": "managed_agent_durable",
                "unattended_enrollment": binding,
                "allowed_mcp_tools": grant.allowed_mcp_tools,
                "runtime_principal": {
                    "type": source_type,
                    "id": identity,
                    "name": f"Managed {app}",
                    "user_id": str(key.user_id),
                },
            },
            commit=False,
        )
        crud_managed_agent_credential.create_for_agent(
            db,
            account_id=key.account_id,
            agent_id=agent.id,
            api_key_id=api_key.id,
            created_by_user_id=key.user_id,
            name="Unattended app credential",
            scopes=api_key.scopes,
            key_prefix=api_key.key_prefix,
            commit=False,
        )
        enrollment = crud_managed_agent_enrollment.create_for_agent(
            db,
            account_id=key.account_id,
            agent_id=agent.id,
            created_by_user_id=key.user_id,
            enrollment_type="cli_managed_config",
            adapter_key=app,
            status="pending",
            validation_result={"state": "pending_local_apply", "attestation": False},
            commit=False,
        )
        crud_api_key.update(
            db,
            db_obj=api_key,
            obj_in={
                "context_data": {
                    **(api_key.context_data or {}),
                    "enrollment_id": str(enrollment.id),
                }
            },
            commit=False,
        )
        credentials.append(
            EnrollmentAppCredential(
                app=app,
                agent_id=agent.id,
                runtime_principal_id=identity,
                enrollment_id=enrollment.id,
                token=token,
                expires_at=expires_at,
            )
        )
    crud_api_key.update(db, db_obj=key, obj_in={"is_active": False}, commit=False)
    _audit(db, key, "app_enrollment_authority_consumed")
    return EnrollmentExchangeResponse(apps=credentials)


def acknowledge_apply(db: Session, *, authenticated_key: models.ApiKey) -> None:
    """Allow one active app key to acknowledge only its own pending config."""
    billing.lock_account(db, str(authenticated_key.account_id))
    key = crud_api_key.get_for_update(
        db, id=authenticated_key.id, account_id=authenticated_key.account_id
    )
    if key is None or not key.is_valid() or key.scopes != ["mcp:read", "mcp:write"]:
        raise HTTPException(403, "Active app credential required")
    context = key.context_data or {}
    if "unattended_enrollment" not in context:
        raise HTTPException(403, "Unattended app credential required")
    agent_id, enrollment_id = (
        context.get("managed_agent_id"),
        context.get("enrollment_id"),
    )
    if not agent_id or not enrollment_id:
        raise HTTPException(403, "Invalid app binding")
    agent = crud_managed_agent.get_for_account(
        db, account_id=str(key.account_id), agent_id=agent_id, for_update=True
    )
    if agent is None or agent.lifecycle_state != "active":
        raise HTTPException(403, "Active app identity required")
    enrollment = crud_managed_agent_enrollment.get_for_agent(
        db,
        account_id=str(key.account_id),
        agent_id=agent_id,
        enrollment_id=enrollment_id,
    )
    if enrollment is None or enrollment.status not in {"pending", "active"}:
        raise HTTPException(409, "App enrollment cannot be reapplied")
    if enrollment.status == "active":
        return
    crud_managed_agent_enrollment.update(
        db,
        db_obj=enrollment,
        obj_in={
            "status": "active",
            "last_applied_at": datetime.now(UTC).replace(tzinfo=None),
            "validation_result": {
                "state": "configured",
                "application_login": "unknown",
                "attestation": False,
            },
        },
        commit=False,
    )
    _audit(db, key, "app_enrollment_configured")
