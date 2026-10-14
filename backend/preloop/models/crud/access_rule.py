"""Account-scoped access-rule storage and detached authorization bundles.

Provider/evaluator code must use this CRUD contract, never endpoint SQL.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Callable, Iterator, cast
from uuid import UUID, uuid4

from sqlalchemy import Engine, event, func, or_, select
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql.dml import Delete

from preloop.models import models
from preloop.models.db.session import get_engine
from preloop.models.models.access_values import SHARE_RESOURCE_TYPES
from preloop.schemas.access_rule import (
    AccessRuleDefinition,
    ResourceAccessDocument,
    ResourceAccessPolicy,
    ResourceAccessUpdate,
)

RULE_FIELDS = tuple(AccessRuleDefinition.model_fields.keys())
RESOURCE_MODELS: dict[str, Any] = {
    "ai_model": models.AIModel,
    "mcp_server": models.MCPServer,
    "managed_agent": models.ManagedAgent,
    "flow": models.Flow,
    "runner": models.FlowRunner,
    "mcp_tool": models.ToolConfiguration,
    "policy": models.PolicySnapshot,
    "policy_baseline": models.PolicySnapshot,
    "tracker": models.Tracker,
    "api_key": models.ApiKey,
    "account": models.Account,
}


class AccessRuleConflictError(ValueError):
    """Missing, inherited or stale policy mutation, safe for an HTTP conflict."""


class AccessReferenceError(ValueError):
    def __init__(self, field: str, reference: str) -> None:
        self.field = field
        super().__init__(
            f"{field}: unknown or ambiguous account-owned reference {reference!r}"
        )


def rule_definition(row: models.AccessRule) -> dict[str, Any]:
    return {key: getattr(row, key) for key in RULE_FIELDS}


def policy_section(db: Session, account_id: UUID) -> dict[str, Any]:
    account = db.get(models.Account, account_id)
    if account is None:
        raise AccessRuleConflictError("account not found")
    rows = db.scalars(
        select(models.AccessRule)
        .where(models.AccessRule.account_id == account_id)
        .order_by(models.AccessRule.priority, models.AccessRule.id)
    ).all()
    return {
        "access_rules": [
            AccessRuleDefinition.model_validate(rule_definition(row)).model_dump(
                mode="json", by_alias=True
            )
            for row in rows
        ],
        "resource_access": [
            policy.model_dump(mode="json") for policy in _policies(account)
        ],
        "access_rule_mode": dict(
            (account.meta_data or {}).get("access_rule_mode") or {}
        ),
    }


def _references(
    db: Session,
    definition: AccessRuleDefinition,
    account: models.Account,
    *,
    resource_ids_validated: bool = False,
) -> AccessRuleDefinition:
    value = definition.model_dump(mode="json", by_alias=True)
    accounts = list(account.hierarchy_path or [account.id])
    for field, model in (("teams", models.Team), ("roles", models.Role)):
        if not value["subject_selector"][field]:
            continue
        scope = model.account_id.in_(accounts)
        if field == "roles":
            scope = or_(scope, model.account_id.is_(None))
        available = db.scalars(select(model).where(scope)).all()
        resolved = []
        for reference in value["subject_selector"][field]:
            matches = [
                row
                for row in available
                if str(row.id) == reference or row.name == reference
            ]
            if len(matches) != 1:
                raise AccessReferenceError(f"subject_selector.{field}", reference)
            resolved.append(str(matches[0].id))
        value["subject_selector"][field] = resolved
    selector = definition.subject_selector
    if selector.ids:
        subject_models: dict[str, Any] = {
            "user": models.User,
            "agent": models.ManagedAgent,
            "api_key": models.ApiKey,
            "flow": models.Flow,
        }
        selected_models = (
            [subject_models[selector.kind]]
            if selector.kind
            else subject_models.values()
        )
        found: set[UUID] = set()
        for subject_model in selected_models:
            found.update(
                db.scalars(
                    select(subject_model.id).where(
                        subject_model.account_id == account.id,
                        subject_model.id.in_(selector.ids),
                    )
                )
            )
        if found != set(selector.ids):
            raise AccessReferenceError("subject_selector.ids", "invalid subject IDs")
    if not resource_ids_validated:
        _resources(
            db, account.id, definition.resource_type, definition.resource_selector.ids
        )
    return AccessRuleDefinition.model_validate(value)


def _resources(
    db: Session, account_id: UUID, resource_type: str | None, resource_ids: list[UUID]
) -> None:
    """Validate a type's exact references with one owner-scoped IN query."""
    if not resource_ids:
        return
    model = RESOURCE_MODELS.get(resource_type) if resource_type is not None else None
    if model is None:
        raise AccessReferenceError("resource_type", str(resource_type))
    owner = model.id if resource_type == "account" else model.account_id
    found = set(
        db.scalars(
            select(model.id).where(model.id.in_(resource_ids), owner == account_id)
        )
    )
    if found != set(resource_ids):
        raise AccessReferenceError("resource_selector.ids", "invalid resource IDs")


def _resource(
    db: Session, account_id: UUID, resource_type: str | None, resource_id: UUID
) -> None:
    _resources(db, account_id, resource_type, [resource_id])


def _validate_resource_references(
    db: Session, account_id: UUID, rules: list[AccessRuleDefinition]
) -> None:
    grouped: dict[str | None, set[UUID]] = defaultdict(set)
    for rule in rules:
        grouped[rule.resource_type].update(rule.resource_selector.ids)
    for resource_type, ids in grouped.items():
        _resources(db, account_id, resource_type, list(ids))


def _policies(account: models.Account) -> list[ResourceAccessPolicy]:
    return [
        ResourceAccessPolicy.model_validate(
            {k: v for k, v in item.items() if k != "version"}
        )
        for item in (account.meta_data or {}).get("resource_access", [])
    ]


def _managed_ids(account: models.Account) -> set[UUID]:
    return {key for policy in _policies(account) for key in policy.rule_ids}


def _supported_action(resource_type: str, action: str) -> bool:
    if action == "resource:view":
        return resource_type in RESOURCE_MODELS
    if action == "resource:share":
        return (
            resource_type in SHARE_RESOURCE_TYPES and resource_type in RESOURCE_MODELS
        )
    return {
        "model:invoke": "ai_model",
        "tool:call": "mcp_tool",
        "flow:run": "flow",
        "runner:accept": "runner",
    }.get(action) == resource_type


def _validate_bindings(
    db: Session,
    account: models.Account,
    policies: list[ResourceAccessPolicy],
    rules: list[AccessRuleDefinition],
) -> None:
    by_id = {rule.id: rule for rule in rules if rule.id}
    identities = [(policy.resource_type, policy.resource_id) for policy in policies]
    ids = [key for policy in policies for key in policy.rule_ids]
    if len(identities) != len(set(identities)) or len(ids) != len(set(ids)):
        raise AccessRuleConflictError(
            "duplicate resource identity or local rule binding"
        )
    for policy in policies:
        _resource(db, account.id, policy.resource_type, policy.resource_id)
        for rule_id in policy.rule_ids:
            rule = by_id.get(rule_id)
            if (
                rule is None
                or rule.effect != "permit"
                or not rule.is_enabled
                or rule.scope != "self"
                or rule.resource_type != policy.resource_type
                or rule.resource_selector.ids != [policy.resource_id]
                or any(
                    not _supported_action(policy.resource_type, action)
                    for action in rule.actions
                )
            ):
                raise AccessRuleConflictError(
                    "local binding requires an enabled owner-local exact-resource permit"
                )
        for requirement in policy.requirements:
            if not _supported_action(policy.resource_type, requirement.action):
                raise AccessRuleConflictError(
                    "unsupported resource action/type combination"
                )
            for rule_id in requirement.permit_rule_ids:
                if (
                    rule_id not in policy.rule_ids
                    or requirement.action not in by_id[rule_id].actions
                ):
                    raise AccessRuleConflictError(
                        "requirement has a dangling or mismatched local permit"
                    )


def _document(
    db: Session, account: models.Account, resource_type: str, resource_id: UUID
) -> ResourceAccessDocument:
    db.flush()
    db.refresh(account, attribute_names=["access_rule_generation"])
    stored: dict[str, Any] = next(
        (
            item
            for item in (account.meta_data or {}).get("resource_access", [])
            if item["resource_type"] == resource_type
            and item["resource_id"] == str(resource_id)
        ),
        {},
    )
    rule_ids = [UUID(key) for key in stored.get("rule_ids", [])]
    rows = db.scalars(
        select(models.AccessRule)
        .where(
            models.AccessRule.account_id == account.id,
            models.AccessRule.id.in_(rule_ids),
        )
        .order_by(models.AccessRule.priority, models.AccessRule.id)
    ).all()
    return ResourceAccessDocument(
        account_id=account.id,
        resource_type=resource_type,
        resource_id=resource_id,
        version=stored.get("version", 0),
        generation=account.access_rule_generation,
        rules=[
            AccessRuleDefinition.model_validate(rule_definition(row)) for row in rows
        ],
        requirements=stored.get("requirements", []),
    )


def _record_change(
    db: Session,
    account_id: UUID,
    user_id: UUID | None,
    action: str,
    details: dict[str, Any],
    resource_type: str = "access_rule",
) -> None:
    from preloop.models.crud.audit_log import crud_audit_log
    from preloop.services.policy.loader import export_current_policy

    db.flush()
    document = export_current_policy(
        db, account_id, include_credentials=True
    ).model_dump(mode="json", exclude_none=True)
    document.update(policy_section(db, account_id))
    version = (
        db.scalar(
            select(func.max(models.PolicySnapshot.version_number)).where(
                models.PolicySnapshot.account_id == account_id
            )
        )
        or 0
    ) + 1
    db.add(
        models.PolicySnapshot(
            account_id=account_id,
            version_number=version,
            description=action,
            snapshot_data=document,
            created_by_user_id=user_id,
            mcp_servers_count=len(document.get("mcp_servers") or []),
            policies_count=len(document.get("approval_workflows") or []),
            tools_count=len(document.get("tools") or []),
        )
    )
    crud_audit_log.log_action(
        db,
        account_id=account_id,
        user_id=user_id,
        action="configuration_change",
        resource_type=resource_type,
        status="success",
        details={"config_type": "access_rules", "operation": action, **details},
        commit=False,
    )


class CRUDAccessRule:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine or get_engine(), expire_on_commit=False) as db:
            yield db

    def rules(self, *, account_id: UUID) -> list[dict[str, Any]]:
        with self.session() as db:
            account = db.get(models.Account, account_id)
            if not account:
                raise AccessRuleConflictError("account not found")
            ancestors = list(account.hierarchy_path or [account.id])[:-1]
            rows = db.scalars(
                select(models.AccessRule)
                .where(
                    or_(
                        models.AccessRule.account_id == account_id,
                        (
                            models.AccessRule.account_id.in_(ancestors)
                            & models.AccessRule.scope.in_(
                                ["subaccounts", "self_and_subaccounts"]
                            )
                        ),
                    )
                )
                .order_by(models.AccessRule.priority, models.AccessRule.id)
            ).all()
            return [
                {
                    **row.to_dict(),
                    "inherited": row.account_id != account_id,
                    "editable": row.account_id == account_id,
                }
                for row in rows
            ]

    def write(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        definition: AccessRuleDefinition,
        rule_id: UUID | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        with self.session() as db, db.begin():
            account = db.scalar(
                select(models.Account)
                .where(models.Account.id == account_id)
                .with_for_update()
            )
            if not account:
                raise AccessRuleConflictError("account not found")
            if rule_id in _managed_ids(account):
                raise AccessRuleConflictError(
                    "edit local rules through the resource access document"
                )
            validated = _references(db, definition, account)
            value = validated.model_dump(mode="json", by_alias=True, exclude={"id"})
            if rule_id:
                row = db.scalar(
                    select(models.AccessRule)
                    .where(
                        models.AccessRule.account_id == account_id,
                        models.AccessRule.id == rule_id,
                    )
                    .with_for_update()
                )
                if (
                    row is None
                    or expected_version is None
                    or row.version != expected_version
                ):
                    raise AccessRuleConflictError(
                        "rule is missing, inherited or changed"
                    )
                for key, field in value.items():
                    setattr(row, key, field)
                row.version += 1
            else:
                row = models.AccessRule(
                    id=uuid4(), account_id=account_id, created_by=user_id, **value
                )
                db.add(row)
            _record_change(
                db, account_id, user_id, "access_rule_saved", {"rule_id": str(row.id)}
            )
            db.flush()
            return row.to_dict()

    def delete(
        self, *, account_id: UUID, user_id: UUID, rule_id: UUID, expected_version: int
    ) -> None:
        with self.session() as db, db.begin():
            account = db.scalar(
                select(models.Account)
                .where(models.Account.id == account_id)
                .with_for_update()
            )
            row = db.scalar(
                select(models.AccessRule)
                .where(
                    models.AccessRule.account_id == account_id,
                    models.AccessRule.id == rule_id,
                )
                .with_for_update()
            )
            if account is None or row is None or row.version != expected_version:
                raise AccessRuleConflictError("rule is missing, inherited or changed")
            if rule_id in _managed_ids(account):
                raise AccessRuleConflictError(
                    "delete local rules through the resource access document"
                )
            db.delete(row)
            _record_change(
                db,
                account_id,
                user_id,
                "access_rule_deleted",
                {"rule_id": str(rule_id)},
            )

    def resource_access(
        self, *, account_id: UUID, resource_type: str, resource_id: UUID
    ) -> ResourceAccessDocument:
        """Read one owner-local resource with consistent version/generation tokens."""
        with self.session() as db:
            db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            account = db.get(models.Account, account_id)
            if account is None:
                raise AccessRuleConflictError("account not found")
            _resource(db, account_id, resource_type, resource_id)
            return _document(db, account, resource_type, resource_id)

    def write_resource_access(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        resource_type: str,
        resource_id: UUID,
        document: ResourceAccessUpdate,
        expected_version: int,
        expected_generation: int,
        losing_subjects: list[dict[str, Any]],
    ) -> ResourceAccessDocument:
        """Apply an EE-validated preview atomically; core does not authorize callers.

        EE must validate preview ownership, inputs, generation and impact before
        calling. Both optimistic tokens are mandatory and checked under owner lock.
        Requirements add independent ceilings and never alter account modes.
        """
        with self.session() as db, db.begin():
            account = db.scalar(
                select(models.Account)
                .where(models.Account.id == account_id)
                .with_for_update()
            )
            if account is None or account.access_rule_generation != expected_generation:
                raise AccessRuleConflictError("policy changed since preview")
            _resource(db, account_id, resource_type, resource_id)
            before = _document(db, account, resource_type, resource_id)
            if before.version != expected_version:
                raise AccessRuleConflictError("resource access document changed")
            _validate_resource_references(db, account_id, document.rules)
            rules = [
                _references(db, rule, account, resource_ids_validated=True)
                for rule in document.rules
            ]
            ids = [rule.id for rule in rules if rule.id is not None]
            if len(ids) != len(rules) or len(ids) != len(set(ids)):
                raise AccessRuleConflictError(
                    "local rules require distinct explicit IDs"
                )
            policy = ResourceAccessPolicy(
                resource_type=resource_type,
                resource_id=resource_id,
                rule_ids=ids,
                requirements=document.requirements,
            )
            others = [
                item
                for item in _policies(account)
                if (item.resource_type, item.resource_id)
                != (resource_type, resource_id)
            ]
            existing = db.scalars(
                select(models.AccessRule).where(
                    models.AccessRule.account_id == account_id
                )
            ).all()
            old_ids = {rule.id for rule in before.rules if rule.id is not None}
            for rule in rules:
                existing_rule = db.get(models.AccessRule, rule.id)
                if existing_rule is not None and (
                    existing_rule.account_id != account_id or rule.id not in old_ids
                ):
                    raise AccessRuleConflictError(
                        "local rule identity is already in use"
                    )
            resulting = [
                AccessRuleDefinition.model_validate(rule_definition(row))
                for row in existing
                if row.id not in old_ids
            ] + rules
            _validate_bindings(db, account, [*others, policy], resulting)
            by_id = {row.id: row for row in existing}
            for rule in rules:
                value = rule.model_dump(mode="json", by_alias=True, exclude={"id"})
                row = by_id.get(rule.id) if rule.id is not None else None
                if row is None:
                    db.add(
                        models.AccessRule(
                            id=rule.id,
                            account_id=account_id,
                            created_by=user_id,
                            **value,
                        )
                    )
                else:
                    for key, field in value.items():
                        setattr(row, key, field)
                    row.version += 1
            for rule_id in old_ids - set(ids):
                db.delete(by_id[rule_id])
            metadata = dict(account.meta_data or {})
            stored = [
                item
                for item in metadata.get("resource_access", [])
                if (item["resource_type"], item["resource_id"])
                != (resource_type, str(resource_id))
            ]
            # Retain an empty tombstone so stale version tokens cannot replay.
            stored.append(
                {**policy.model_dump(mode="json"), "version": before.version + 1}
            )
            metadata["resource_access"] = stored
            account.meta_data = metadata
            _record_change(
                db,
                account_id,
                user_id,
                "resource_access_saved",
                {
                    "resource_type": resource_type,
                    "resource_id": str(resource_id),
                    "losing_subjects": losing_subjects,
                },
                resource_type="resource_access",
            )
            return _document(db, account, resource_type, resource_id)

    def set_mode(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        action: str,
        mode: str,
        expected_generation: int,
        losing_subjects: list[dict[str, Any]],
    ) -> None:
        from preloop.schemas.access_rule import Action, Mode
        from pydantic import TypeAdapter

        TypeAdapter(Action).validate_python(action)
        TypeAdapter(Mode).validate_python(mode)
        with self.session() as db, db.begin():
            account = db.scalar(
                select(models.Account)
                .where(models.Account.id == account_id)
                .with_for_update()
            )
            if account is None or account.access_rule_generation != expected_generation:
                raise AccessRuleConflictError("policy changed since preview")
            metadata = dict(account.meta_data or {})
            modes = dict(metadata.get("access_rule_mode") or {})
            before = modes.get(action, "additive")
            if before == mode:
                raise AccessRuleConflictError(
                    "mode already saved; preview cannot be replayed"
                )
            modes[action] = mode
            metadata["access_rule_mode"] = modes
            account.meta_data = metadata
            _record_change(
                db,
                account_id,
                user_id,
                "access_rule_mode_changed",
                {
                    "action": action,
                    "before": before,
                    "after": mode,
                    "losing_subjects": losing_subjects,
                },
            )

    def generation(self, *, account_id: UUID) -> int:
        with self.session() as db:
            generation = db.scalar(
                select(models.Account.access_rule_generation).where(
                    models.Account.id == account_id
                )
            )
            if generation is None:
                raise AccessRuleConflictError("account not found")
            return int(generation)

    def bundle(self, *, account_id: UUID) -> dict[str, Any]:
        """Load one consistent, secret-free account snapshot for an evaluator cache."""
        with self.session() as db:
            db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            current = db.get(models.Account, account_id)
            if not current:
                raise AccessRuleConflictError("account not found")
            path = list(current.hierarchy_path or [current.id])
            account_rows = db.scalars(
                select(models.Account).where(models.Account.id.in_(path))
            ).all()
            accounts = {
                str(row.id): {
                    "id": str(row.id),
                    "path": [str(key) for key in row.hierarchy_path],
                    "tags": {},
                }
                for row in account_rows
            }
            tags: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
            for row in db.scalars(
                select(models.ResourceTag).where(
                    models.ResourceTag.account_id.in_(path)
                )
            ):
                tags[(row.resource_type, str(row.resource_id))][row.key] = row.value
            for key, account in accounts.items():
                account["tags"] = tags.get(("account", key), {})
            users = db.scalars(
                select(models.User).where(models.User.account_id.in_(path))
            ).all()
            persons = db.scalars(
                select(models.Person).where(
                    models.Person.id.in_([user.person_id for user in users])
                )
            ).all()
            primary = {str(person.id): person.primary_user_id for person in persons}
            primary_users = db.scalars(
                select(models.User).where(
                    models.User.id.in_([key for key in primary.values() if key])
                )
            ).all()
            user_accounts = {
                str(user.id): str(user.account_id) for user in [*users, *primary_users]
            }
            home_ids = list({user.account_id for user in primary_users} - set(path))
            for row in db.scalars(
                select(models.Account).where(models.Account.id.in_(home_ids))
            ):
                accounts[str(row.id)] = {
                    "id": str(row.id),
                    "path": [str(key) for key in row.hierarchy_path],
                    "tags": {},
                }
            for row in db.scalars(
                select(models.ResourceTag).where(
                    models.ResourceTag.account_id.in_(home_ids),
                    models.ResourceTag.resource_type == "account",
                )
            ):
                accounts[str(row.resource_id)]["tags"][row.key] = row.value
            teams: dict[str, set[str]] = defaultdict(set)
            roles: dict[str, set[str]] = defaultdict(set)
            user_ids = [user.id for user in users]
            for row in db.scalars(
                select(models.TeamMembership)
                .join(models.Team, models.Team.id == models.TeamMembership.team_id)
                .where(
                    models.TeamMembership.user_id.in_(user_ids),
                    models.Team.account_id.in_(path),
                )
            ):
                teams[str(row.user_id)].add(str(row.team_id))
            grants = {
                str(row.id): row
                for row in db.scalars(
                    select(models.AccountAccessGrant).where(
                        models.AccountAccessGrant.parent_account_id.in_(path),
                        models.AccountAccessGrant.revoked_at.is_(None),
                    )
                )
            }
            for user in users:
                grant = grants.get(str(user.access_grant_id))
                if (
                    user.membership_kind == "inherited"
                    and grant
                    and grant.subject_type == "team"
                ):
                    teams[str(user.id)].add(str(grant.subject_id))
            for row in db.scalars(
                select(models.UserRole)
                .join(models.Role, models.Role.id == models.UserRole.role_id)
                .where(
                    models.UserRole.user_id.in_(user_ids),
                    or_(
                        models.Role.account_id.in_(path),
                        models.Role.account_id.is_(None),
                    ),
                )
            ):
                roles[str(row.user_id)].add(str(row.role_id))
            team_roles = db.scalars(
                select(models.TeamRole)
                .join(models.Role, models.Role.id == models.TeamRole.role_id)
                .where(
                    models.TeamRole.team_id.in_(
                        {UUID(team) for values in teams.values() for team in values}
                    ),
                    or_(
                        models.Role.account_id.in_(path),
                        models.Role.account_id.is_(None),
                    ),
                )
            ).all()
            for user_id, held in teams.items():
                roles[user_id].update(
                    str(row.role_id) for row in team_roles if str(row.team_id) in held
                )
            subjects: dict[tuple[str, str], dict[str, Any]] = {}
            for user in users:
                if user.account_id != account_id:
                    continue
                key = str(user.id)
                home = user_accounts.get(
                    str(primary.get(str(user.person_id))), str(account_id)
                )
                subjects[("user", key)] = {
                    "kind": "user",
                    "id": key,
                    "roles": sorted(roles[key]),
                    "teams": sorted(teams[key]),
                    "membership_kind": user.membership_kind,
                    "home_account": accounts.get(home, accounts[str(account_id)]),
                    "account": accounts[str(account_id)],
                    "tags": {},
                }
            resources = {}
            for kind, model in RESOURCE_MODELS.items():
                if kind == "account":
                    continue
                # Select identifiers only: owner tokens, agent system metadata and
                # arbitrary customer config never enter this cache or explain DTOs.
                columns = [model.id, model.account_id]
                if kind == "runner":
                    columns.append(model.labels)
                visible = model.account_id.in_(path)
                if kind in ("ai_model", "mcp_server"):
                    visible = or_(visible, model.account_id.is_(None))
                for row in db.execute(select(*columns).where(visible)):
                    key = str(row.id)
                    resources[(kind, key)] = {
                        "type": kind,
                        "id": key,
                        "account_id": str(row.account_id),
                        "account": accounts.get(
                            str(row.account_id), {"id": None, "tags": {}, "path": []}
                        ),
                        "tags": tags.get((kind, key), {}),
                        "labels": row.labels if kind == "runner" else {},
                    }
                    if row.account_id == account_id and kind in (
                        "managed_agent",
                        "flow",
                        "api_key",
                    ):
                        subject_kind = "agent" if kind == "managed_agent" else kind
                        subjects[(subject_kind, key)] = {
                            "kind": subject_kind,
                            "id": key,
                            "roles": [],
                            "teams": [],
                            "membership_kind": "direct",
                            "account": accounts[str(account_id)],
                            "home_account": accounts[str(account_id)],
                            "tags": tags.get((kind, key), {}),
                        }
            shared_agents = db.scalars(
                select(models.ManagedAgent)
                .join(
                    models.ResourceShareRecipient,
                    models.ResourceShareRecipient.resource_id == models.ManagedAgent.id,
                )
                .where(
                    models.ResourceShareRecipient.recipient_account_id == account_id,
                    models.ResourceShareRecipient.resource_type == "managed_agent",
                )
                .distinct()
            ).all()
            from preloop.models.crud.resource_share import crud_resource_share

            # Issued consumer-session credentials preserve their agent identity
            # after rescinding; only new targeting depends on live recipients.
            by_id = {agent.id: agent for agent in shared_agents}
            for key in db.scalars(
                select(models.ApiKey).where(
                    models.ApiKey.account_id == account_id,
                    models.ApiKey.context_data["shared_agent_owner_account_id"]
                    .as_string()
                    .is_not(None),
                )
            ):
                agent = crud_resource_share.bound_agent(db, key=key)
                if agent is not None:
                    by_id[agent.id] = agent
            shared_agents = list(by_id.values())
            # A detached consumer may still finish an issued session; its
            # former owner is no longer in the hierarchy path.
            missing_owners = {
                agent.account_id
                for agent in shared_agents
                if str(agent.account_id) not in accounts
            }
            for owner in db.scalars(
                select(models.Account).where(models.Account.id.in_(missing_owners))
            ):
                accounts[str(owner.id)] = {
                    "id": str(owner.id),
                    "path": [str(key) for key in owner.hierarchy_path],
                    "tags": {},
                }
            shared_agent_ids = {
                str(agent.id): str(agent.account_id) for agent in shared_agents
            }
            for agent in shared_agents:
                key = str(agent.id)
                subjects[("agent", key)] = {
                    "kind": "agent",
                    "id": key,
                    "roles": [],
                    "teams": [],
                    "membership_kind": "inherited",
                    "account": accounts[str(account_id)],
                    "home_account": accounts[str(agent.account_id)],
                    "tags": tags.get(("managed_agent", key), {}),
                }
            key_agents = {
                str(row.api_key_id): str(row.managed_agent_id)
                for row in db.scalars(
                    select(models.ManagedAgentCredential).where(
                        models.ManagedAgentCredential.account_id == account_id,
                        models.ManagedAgentCredential.status == "active",
                    )
                )
            }
            agents_by_source = {
                (row.session_source_type, row.session_source_id): str(row.id)
                for row in db.scalars(
                    select(models.ManagedAgent).where(
                        models.ManagedAgent.account_id == account_id
                    )
                )
            }
            runtime_agents = {
                str(row.id): agents_by_source.get(
                    (row.session_source_type, row.session_source_id)
                )
                for row in db.execute(
                    select(
                        models.RuntimeSession.id,
                        models.RuntimeSession.session_source_type,
                        models.RuntimeSession.session_source_id,
                    ).where(models.RuntimeSession.account_id == account_id)
                )
            }
            for row in db.execute(
                select(
                    models.ApiKey.id, models.ApiKey.user_id, models.ApiKey.context_data
                ).where(models.ApiKey.account_id == account_id)
            ):
                subject = subjects.get(("api_key", str(row.id)))
                owner = subjects.get(("user", str(row.user_id)))
                if subject and owner:
                    for attribute in (
                        "roles",
                        "teams",
                        "membership_kind",
                        "home_account",
                    ):
                        subject[attribute] = owner[attribute]
                shared_id = str((row.context_data or {}).get("managed_agent_id") or "")
                shared_owner = str(
                    (row.context_data or {}).get("shared_agent_owner_account_id") or ""
                )
                if shared_agent_ids.get(shared_id) == shared_owner:
                    key_agents[str(row.id)] = shared_id
                if str(row.id) not in key_agents:
                    runtime = str(
                        (row.context_data or {}).get("runtime_session_id") or ""
                    )
                    if runtime_agents.get(runtime):
                        key_agents[str(row.id)] = runtime_agents[runtime]
            rules = db.scalars(
                select(models.AccessRule)
                .where(
                    models.AccessRule.account_id.in_(path),
                    models.AccessRule.is_enabled.is_(True),
                )
                .order_by(models.AccessRule.priority, models.AccessRule.id)
            ).all()
            local_ids = {key for owner in account_rows for key in _managed_ids(owner)}
            local_rules = [
                {**rule_definition(row), "account_id": str(row.account_id)}
                for row in rules
                if row.id in local_ids
            ]
            applicable = [
                {**rule_definition(row), "account_id": str(row.account_id)}
                for row in rules
                if row.id not in local_ids
                and (
                    (row.account_id == account_id and row.scope != "subaccounts")
                    or (row.account_id != account_id and row.scope != "self")
                )
            ]
            modes = {}
            ancestor_require_permit = set()
            for row in account_rows:
                for action, mode in (
                    (row.meta_data or {}).get("access_rule_mode") or {}
                ).items():
                    if row.id != account_id and mode == "require_permit":
                        ancestor_require_permit.add(action)
                    if mode == "require_permit" or action not in modes:
                        modes[action] = mode
            return {
                "account_id": str(account_id),
                "generation": current.access_rule_generation,
                "accounts": accounts,
                "rules": applicable,
                "modes": modes,
                "ancestor_require_permit": sorted(ancestor_require_permit),
                "resource_access_rules": local_rules,
                "resource_access": [
                    {**policy.model_dump(mode="json"), "account_id": str(owner.id)}
                    for owner in account_rows
                    for policy in _policies(owner)
                ],
                "subjects": subjects,
                "resources": resources,
                "key_agents": key_agents,
            }

    def listen(
        self,
        callback: Callable[[str, int], None],
        ready: threading.Event,
        stop: threading.Event,
    ) -> None:
        """Consume committed generation events on a connection that is not pooled.

        The shared engine's checkout would return to the pool still in
        autocommit with LISTEN registered. A private NullPool engine is
        discarded with the listener.
        """
        import select
        from contextlib import closing

        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        source = self.engine or get_engine()
        engine = create_engine(source.url, poolclass=NullPool)
        try:
            with closing(engine.raw_connection()) as connection:
                driver = connection.driver_connection
                driver.autocommit = True
                with driver.cursor() as cursor:
                    cursor.execute("LISTEN preloop_access")
                ready.set()
                notifies = getattr(driver, "notifies", None)
                while not stop.is_set():
                    if callable(notifies):
                        for notification in notifies(timeout=1, stop_after=1):
                            _deliver_access_notification(notification, callback)
                        continue
                    select.select([driver], [], [], 1)
                    driver.poll()
                    while driver.notifies:
                        _deliver_access_notification(driver.notifies.pop(0), callback)
        finally:
            engine.dispose()


def _deliver_access_notification(notification: Any, callback: Callable) -> None:
    data = json.loads(notification.payload)
    callback(str(data["account_id"]), int(data["generation"]))


crud_access_rule = CRUDAccessRule()


def validate_policy_section(
    db: Session,
    account_id: UUID,
    rules: list[AccessRuleDefinition] | None,
    modes: dict[str, str] | None,
    resource_access: list[ResourceAccessPolicy] | None = None,
) -> list[AccessRuleDefinition]:
    """Validate import without letting YAML bypass audited mode preview."""
    account = db.get(models.Account, account_id)
    if not account:
        raise AccessRuleConflictError("account not found")
    current = (account.meta_data or {}).get("access_rule_mode") or {}
    if any(
        mode == "require_permit" and current.get(action) != mode
        for action, mode in (modes or {}).items()
    ):
        raise AccessRuleConflictError(
            "require_permit requires the access mode explain preview endpoint"
        )
    _validate_resource_references(db, account_id, rules or [])
    validated = [
        _references(db, definition, account, resource_ids_validated=True)
        for definition in rules or []
    ]
    identities = [str(rule.id) for rule in validated if rule.id]
    names = [rule.name for rule in validated]
    if len(identities) != len(set(identities)) or len(names) != len(set(names)):
        raise AccessRuleConflictError("duplicate access rule id or name")
    for rule in validated:
        if rule.id:
            row = db.get(models.AccessRule, rule.id)
            if row is not None and row.account_id != account_id:
                raise AccessRuleConflictError(
                    "access rule identity belongs to another account"
                )
    current_policies = _policies(account)
    incoming = current_policies if resource_access is None else resource_access
    current_by_key = {
        (item.resource_type, item.resource_id): item for item in current_policies
    }
    for item in incoming:
        before = current_by_key.get((item.resource_type, item.resource_id))
        if item.requirements and (
            before is None or item.requirements != before.requirements
        ):
            raise AccessRuleConflictError(
                "resource require_permit requires the resource access preview application path"
            )
    resulting = (
        validated
        if rules is not None
        else [
            AccessRuleDefinition.model_validate(rule_definition(row))
            for row in db.scalars(
                select(models.AccessRule).where(
                    models.AccessRule.account_id == account_id
                )
            )
        ]
    )
    _validate_bindings(db, account, incoming, resulting)
    resulting_by_id = {rule.id: rule for rule in resulting}
    incoming_ids = {key for item in incoming for key in item.rule_ids}
    detached = _managed_ids(account) - incoming_ids
    if detached.intersection(resulting_by_id):
        raise AccessRuleConflictError(
            "remove detached local permits with their resource binding"
        )
    # Editing permit subjects/conditions can tighten an existing local gate just
    # as changing its links can. YAML roundtrips are allowed; edits use preview.
    for item in incoming:
        if not item.requirements:
            continue
        for rule_id in item.rule_ids:
            stored = db.get(models.AccessRule, rule_id)
            if stored is not None and resulting_by_id[
                rule_id
            ] != AccessRuleDefinition.model_validate(rule_definition(stored)):
                raise AccessRuleConflictError(
                    "local permit changes require the resource access preview application path"
                )
    return validated


def apply_policy_section(
    db: Session,
    account_id: UUID,
    user_id: UUID | None,
    rules: list[AccessRuleDefinition] | None,
    modes: dict[str, str] | None,
    resource_access: list[ResourceAccessPolicy] | None = None,
) -> None:
    account = db.scalar(
        select(models.Account).where(models.Account.id == account_id).with_for_update()
    )
    if not account:
        raise AccessRuleConflictError("account not found")
    validated = validate_policy_section(db, account_id, rules, modes, resource_access)
    if rules is not None:
        existing = db.scalars(
            select(models.AccessRule).where(models.AccessRule.account_id == account_id)
        ).all()
        by_id = {row.id: row for row in existing}
        by_name = {row.name: row for row in existing}
        retained = set()
        for rule in validated:
            row = by_id.get(rule.id) if rule.id else by_name.get(rule.name)
            value = rule.model_dump(mode="json", by_alias=True, exclude={"id"})
            if row:
                for key, field in value.items():
                    setattr(row, key, field)
                row.version += 1
            else:
                row = models.AccessRule(
                    id=rule.id or uuid4(),
                    account_id=account_id,
                    created_by=user_id,
                    **value,
                )
                db.add(row)
            retained.add(row.id)
        for row in existing:
            if row.id not in retained:
                db.delete(row)
    if modes is not None:
        metadata = dict(account.meta_data or {})
        metadata["access_rule_mode"] = dict(modes)
        account.meta_data = metadata
    if resource_access is not None:
        metadata = dict(account.meta_data or {})
        versions = {
            (item["resource_type"], item["resource_id"]): item.get("version", 0)
            for item in metadata.get("resource_access", [])
        }
        metadata["resource_access"] = [
            {
                **item.model_dump(mode="json"),
                "version": versions.get((item.resource_type, str(item.resource_id)), 0)
                + 1,
            }
            for item in resource_access
        ]
        account.meta_data = metadata
    _record_change(db, account_id, user_id, "access_rules_imported", {})


# Keep deleted identities and their local documents in the same transaction.
# Registration is unconditional through models.crud, including OSS processes and
# fresh workers that have not loaded an evaluator or read a resource document.
_DELETED_RESOURCES = "access_rule_deleted_resources"


def _collect_deleted_resources(db: Session, *_: Any) -> None:
    for row in db.deleted:
        for kind, model in RESOURCE_MODELS.items():
            if (
                kind != "account"
                and isinstance(row, model)
                and row.account_id is not None
            ):
                db.info.setdefault(_DELETED_RESOURCES, set()).add(
                    (row.account_id, kind, row.id)
                )


def _collect_bulk_deleted_resources(state: ORMExecuteState) -> None:
    if not state.is_delete or state.bind_mapper is None:
        return
    for kind, model in RESOURCE_MODELS.items():
        if kind == "account" or state.bind_mapper.class_ is not model:
            continue
        query = select(model.account_id, model.id)
        statement = cast(Delete, state.statement)
        if statement.whereclause is not None:
            query = query.where(statement.whereclause)
        with state.session.no_autoflush:
            for owner, resource_id in state.session.execute(query):
                if owner is not None:
                    state.session.info.setdefault(_DELETED_RESOURCES, set()).add(
                        (owner, kind, resource_id)
                    )


def _cleanup_deleted_resources(db: Session) -> None:
    if db.in_nested_transaction():
        return  # The outer commit owns cleanup; a savepoint may still roll back.
    db.flush()  # Collect ORM deletes even when the caller commits without flushing.
    targets = db.info.pop(_DELETED_RESOURCES, set())
    grouped: dict[UUID, dict[str, set[UUID]]] = defaultdict(lambda: defaultdict(set))
    for owner, kind, resource_id in targets:
        grouped[owner][kind].add(resource_id)
    for owner in sorted(grouped, key=str):
        account = db.scalar(
            select(models.Account)
            .where(models.Account.id == owner)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            continue  # The owning account itself was deleted.
        candidates = grouped[owner]
        metadata = dict(account.meta_data or {})
        before = metadata.get("resource_access", [])
        rows = list(
            db.scalars(
                select(models.AccessRule).where(models.AccessRule.account_id == owner)
            )
        )
        for item in before:
            candidates[item["resource_type"]].add(UUID(item["resource_id"]))
        for row in rows:
            if row.resource_type is None:
                continue
            candidates[row.resource_type].update(
                UUID(key) for key in (row.resource_selector or {}).get("ids", [])
            )
        removed: dict[str, set[UUID]] = {}
        for kind, ids in candidates.items():
            if not ids:
                continue
            model = RESOURCE_MODELS[kind]
            owner_column = model.id if kind == "account" else model.account_id
            found = set(
                db.scalars(
                    select(model.id).where(model.id.in_(ids), owner_column == owner)
                )
            )
            missing = ids - found
            if missing:
                removed[kind] = missing
        retained = [
            item
            for item in before
            if UUID(item["resource_id"])
            not in removed.get(item["resource_type"], set())
        ]
        changed = len(retained) != len(before)
        if changed:
            metadata["resource_access"] = retained
            account.meta_data = metadata
        for row in rows:
            selector = dict(row.resource_selector or {})
            ids = selector.get("ids", [])
            remaining = [
                key
                for key in ids
                if UUID(key) not in removed.get(row.resource_type or "", set())
            ]
            if len(remaining) == len(ids):
                continue
            changed = True
            if remaining:
                selector["ids"] = remaining
                row.resource_selector = selector
                row.version += 1
            else:
                # Never turn an emptied exact-ID selector into a broad permit.
                db.delete(row)
        if changed:
            _record_change(
                db,
                owner,
                None,
                "resource_access_target_deleted",
                {
                    "resources": [
                        {"resource_type": kind, "resource_id": str(key)}
                        for kind, ids in sorted(removed.items())
                        for key in sorted(ids, key=str)
                    ]
                },
                resource_type="resource_access",
            )


def _clear_deleted_resources(db: Session, transaction: Any) -> None:
    # Preserve outer deletes across a rolled-back savepoint. The cleanup also
    # rechecks existence so rolled-back inner deletions cannot prune live gates.
    if transaction.parent is None:
        db.info.pop(_DELETED_RESOURCES, None)


event.listen(Session, "before_flush", _collect_deleted_resources)
event.listen(Session, "do_orm_execute", _collect_bulk_deleted_resources)
event.listen(Session, "before_commit", _cleanup_deleted_resources)
event.listen(Session, "after_soft_rollback", _clear_deleted_resources)
