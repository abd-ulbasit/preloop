"""Portable exact-resource gates, atomic owner writes and preview discipline."""

from __future__ import annotations

import threading
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from preloop.models import models
from preloop.models.crud.access_rule import (
    AccessReferenceError,
    AccessRuleConflictError,
    apply_policy_section,
    validate_policy_section,
)
from preloop.schemas.access_rule import (
    AccessRuleDefinition,
    ResourceAccessRequirement,
    ResourceAccessUpdate,
)
from preloop.services.policy.loader import (
    compute_policy_diff,
    export_current_policy,
    load_policy_from_string,
)

from .test_access_rule import storage  # noqa: F401 - shared PostgreSQL fixture


@pytest.fixture
def resource(storage: Any) -> Any:  # noqa: F811 - imported shared fixture
    crud, owners = storage
    account, user = owners[0]
    ids = []
    with crud.session() as db, db.begin():
        for owner, _ in owners:
            row = models.AIModel(
                account_id=owner,
                name="Example model",
                provider_name="example",
                model_identifier="example-model",
                api_key="synthetic-secret",
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
    try:
        yield crud, owners, ids
    finally:
        with crud.session() as db, db.begin():
            for key in ids:
                row = db.get(models.AIModel, key)
                if row:
                    db.delete(row)


def local_rule(resource_id: UUID, **values: Any) -> AccessRuleDefinition:
    raw = dict(
        id=uuid4(),
        name="Example local permit",
        effect="permit",
        actions=["model:invoke"],
        resource_type="ai_model",
        resource_selector={"ids": [str(resource_id)]},
    )
    raw.update(values)
    return AccessRuleDefinition.model_validate(raw)


def update(rule: AccessRuleDefinition, restricted: bool = True) -> ResourceAccessUpdate:
    return ResourceAccessUpdate(
        rules=[rule],
        requirements=[
            ResourceAccessRequirement(action="model:invoke", permit_rule_ids=[rule.id])
        ]
        if restricted
        else [],
    )


def save(resource: Any, document: ResourceAccessUpdate, **tokens: Any) -> Any:
    crud, owners, ids = resource
    account, user = owners[0]
    current = crud.resource_access(
        account_id=account, resource_type="ai_model", resource_id=ids[0]
    )
    return crud.write_resource_access(
        account_id=account,
        user_id=user,
        resource_type="ai_model",
        resource_id=ids[0],
        document=document,
        expected_version=tokens.get("version", current.version),
        expected_generation=tokens.get("generation", current.generation),
        losing_subjects=[],
    )


@pytest.mark.parametrize(
    "selector,type_",
    [
        ({"ids": [str(uuid4())]}, None),
        ({"ids": ["invalid"]}, "ai_model"),
        ({"ids": ["11111111-1111-4111-8111-111111111111"] * 2}, "ai_model"),
        ({"ids": [str(uuid4()) for _ in range(101)]}, "ai_model"),
    ],
)
def test_reject_invalid_resource_ids(selector: Any, type_: Any) -> None:
    with pytest.raises(ValidationError):
        AccessRuleDefinition(
            name="Example",
            effect="permit",
            actions=["model:invoke"],
            resource_selector=selector,
            resource_type=type_,
        )


def test_legacy_and_ids_plus_tags_roundtrip() -> None:
    legacy = AccessRuleDefinition(name="Legacy", effect="forbid", actions=["tool:call"])
    assert legacy.resource_selector.ids == []
    key = uuid4()
    parsed = AccessRuleDefinition.model_validate(
        {
            "name": "Intersection",
            "effect": "permit",
            "actions": ["model:invoke"],
            "resource": {
                "type": "ai_model",
                "ids": [str(key)],
                "matchLabels": {"env": "production"},
                "account": {"matchLabels": {"group": "example"}},
            },
        }
    )
    assert parsed.resource_selector.ids == [key]
    assert parsed.resource_selector.match_labels == {"env": "production"}
    assert parsed.resource_selector.account.match_labels == {"group": "example"}
    assert AccessRuleDefinition.model_validate_json(parsed.model_dump_json()) == parsed


def test_owner_type_and_subject_references(resource: Any) -> None:
    crud, owners, ids = resource
    account, user = owners[0]
    for rule in (
        local_rule(ids[1]),
        local_rule(ids[0], resource_type="mcp_server"),
        local_rule(
            ids[0], subject_selector={"kind": "user", "ids": [str(owners[1][1])]}
        ),
        local_rule(ids[0], subject_selector={"teams": [str(uuid4())]}),
        local_rule(ids[0], subject_selector={"roles": [str(uuid4())]}),
    ):
        with pytest.raises(AccessReferenceError):
            save(resource, update(rule))
    valid = local_rule(ids[0], subject_selector={"kind": "user", "ids": [str(user)]})
    assert save(resource, update(valid)).rules[0].subject_selector.ids == [user]
    with pytest.raises(AccessReferenceError):
        crud.resource_access(
            account_id=owners[1][0], resource_type="ai_model", resource_id=ids[0]
        )


def test_atomic_local_write_version_metadata_and_no_account_mode_mutation(
    resource: Any,
) -> None:
    crud, owners, ids = resource
    account, user = owners[0]
    broad = crud.write(
        account_id=account,
        user_id=user,
        definition=AccessRuleDefinition(
            name="Broad permit", effect="permit", actions=["model:invoke"]
        ),
    )
    with crud.session() as db, db.begin():
        row = db.get(models.Account, account)
        row.meta_data = {
            "unrelated": {"setting": 17},
            "access_rule_mode": {"model:invoke": "require_permit"},
        }
    initial = crud.resource_access(
        account_id=account, resource_type="ai_model", resource_id=ids[0]
    )
    assert initial.version == 0 and initial.requirements == []
    rule = local_rule(ids[0])
    saved = save(resource, update(rule))
    assert saved.version == 1 and saved.generation > initial.generation
    assert saved.requirements[0].permit_rule_ids == [rule.id]
    with crud.session() as db:
        metadata = db.get(models.Account, account).meta_data
        assert metadata["unrelated"] == {"setting": 17}
        assert metadata["access_rule_mode"] == {"model:invoke": "require_permit"}
        snapshot = db.scalars(
            select(models.PolicySnapshot)
            .where(models.PolicySnapshot.account_id == account)
            .order_by(models.PolicySnapshot.version_number.desc())
        ).first()
        assert snapshot.snapshot_data["resource_access"][0]["requirements"][0][
            "permit_rule_ids"
        ] == [str(rule.id)]
    with pytest.raises(AccessRuleConflictError):
        save(resource, update(rule), version=0)
    with pytest.raises(AccessRuleConflictError):
        save(resource, update(rule), generation=initial.generation)
    for operation in ("write", "delete"):
        with pytest.raises(AccessRuleConflictError, match="resource access document"):
            if operation == "write":
                crud.write(
                    account_id=account,
                    user_id=user,
                    rule_id=rule.id,
                    expected_version=1,
                    definition=rule,
                )
            else:
                crud.delete(
                    account_id=account,
                    user_id=user,
                    rule_id=rule.id,
                    expected_version=1,
                )
    cleared = save(resource, ResourceAccessUpdate())
    assert cleared.version == 2 and cleared.requirements == [] and cleared.rules == []
    assert [row["id"] for row in crud.rules(account_id=account)] == [broad["id"]]


@pytest.mark.parametrize(
    "change",
    [
        {"resource_selector": {}},
        {"scope": "self_and_subaccounts"},
        {"effect": "forbid"},
        {"is_enabled": False},
        {"actions": ["tool:call"]},
        {"resource_selector": {"ids": []}},
    ],
)
def test_restrictions_cannot_bind_broad_or_mismatched_rules(
    resource: Any, change: Any
) -> None:
    crud, owners, ids = resource
    account, _ = owners[0]
    generation = crud.generation(account_id=account)
    with pytest.raises(AccessRuleConflictError):
        save(resource, update(local_rule(ids[0], **change)))
    assert crud.generation(account_id=account) == generation
    assert crud.rules(account_id=account) == []


def test_dangling_foreign_duplicate_and_wrong_action_bindings(resource: Any) -> None:
    crud, owners, ids = resource
    rule = local_rule(ids[0])
    with pytest.raises(ValidationError):
        save(
            resource,
            ResourceAccessUpdate(
                rules=[rule],
                requirements=[
                    ResourceAccessRequirement(
                        action="model:invoke", permit_rule_ids=[uuid4()]
                    )
                ],
            ),
        )
    with pytest.raises(ValidationError):
        save(
            resource,
            ResourceAccessUpdate(
                rules=[rule],
                requirements=[
                    ResourceAccessRequirement(action="model:invoke"),
                    ResourceAccessRequirement(action="model:invoke"),
                ],
            ),
        )
    with pytest.raises(AccessRuleConflictError):
        save(resource, ResourceAccessUpdate(rules=[rule, rule]))
    with pytest.raises(AccessRuleConflictError, match="mismatched"):
        save(
            resource,
            ResourceAccessUpdate(
                rules=[rule],
                requirements=[
                    ResourceAccessRequirement(
                        action="resource:view", permit_rule_ids=[rule.id]
                    )
                ],
            ),
        )
    foreign = crud.write(
        account_id=owners[1][0], user_id=owners[1][1], definition=local_rule(ids[1])
    )
    with pytest.raises(AccessRuleConflictError, match="already in use"):
        save(resource, update(local_rule(ids[0], id=foreign["id"])))


def test_team_selection_alone_inherits_and_empty_restriction_denies_all(
    resource: Any,
) -> None:
    crud, owners, ids = resource
    with crud.session() as db, db.begin():
        team = models.Team(account_id=owners[0][0], name="Example team")
        db.add(team)
        db.flush()
        team_id = team.id
    saved = save(
        resource,
        update(local_rule(ids[0], subject_selector={"teams": [str(team_id)]}), False),
    )
    assert saved.requirements == []
    assert saved.rules[0].subject_selector.teams == [str(team_id)]
    restricted = save(
        resource,
        ResourceAccessUpdate(
            requirements=[ResourceAccessRequirement(action="model:invoke")]
        ),
    )
    assert restricted.requirements[0].permit_rule_ids == []


def test_export_import_preserves_and_rejects_tightening_and_dangling_removal(
    resource: Any,
) -> None:
    crud, owners, ids = resource
    account, user = owners[0]
    with crud.session() as db:
        original = export_current_policy(db, account)
    rule = local_rule(ids[0])
    saved = save(resource, update(rule))
    with crud.session() as db:
        exported = export_current_policy(db, account)
        assert exported.resource_access[0].resource_id == ids[0]
        paths = [item.path for item in compute_policy_diff(original, exported).changes]
        assert "$.resource_access" in paths
        parsed, validation = load_policy_from_string(exported.model_dump_json())
        assert validation.is_valid
        assert parsed is not None
        assert parsed.resource_access == exported.resource_access
    with crud.session() as db, db.begin():
        apply_policy_section(
            db,
            account,
            user,
            exported.access_rules,
            exported.access_rule_mode,
            exported.resource_access,
        )
    with crud.session() as db:
        with pytest.raises(AccessRuleConflictError, match="preview"):
            changed = exported.resource_access[0].model_copy(
                update={
                    "requirements": [ResourceAccessRequirement(action="model:invoke")]
                }
            )
            validate_policy_section(db, account, exported.access_rules, None, [changed])
        with pytest.raises(AccessRuleConflictError, match="exact-resource"):
            validate_policy_section(db, account, [], None)
    save(resource, ResourceAccessUpdate())
    with crud.session() as db:
        with pytest.raises(AccessRuleConflictError, match="preview"):
            validate_policy_section(
                db, account, exported.access_rules, None, exported.resource_access
            )
    assert saved.generation > 0


def test_bundle_preserves_independent_account_ancestor_and_local_gates(
    resource: Any,
) -> None:
    crud, owners, ids = resource
    account, user = owners[0]
    child, _ = owners[1]
    with crud.session() as db, db.begin():
        row = db.get(models.Account, child)
        row.parent_account_id = account
        row.hierarchy_path = [account, child]
        row.root_account_id = account
        row.hierarchy_depth = 1
    saved = save(resource, update(local_rule(ids[0])))
    crud.set_mode(
        account_id=account,
        user_id=user,
        action="model:invoke",
        mode="require_permit",
        expected_generation=crud.generation(account_id=account),
        losing_subjects=[],
    )
    broad = crud.write(
        account_id=account,
        user_id=user,
        definition=AccessRuleDefinition(
            name="Broad parent permit",
            effect="permit",
            actions=["model:invoke"],
            scope="self_and_subaccounts",
        ),
    )
    bundle = crud.bundle(account_id=child)
    binding = bundle["resource_access"][0]
    assert binding["account_id"] == str(account)
    assert binding["resource_id"] == str(ids[0])
    assert binding["requirements"][0]["permit_rule_ids"] == [str(saved.rules[0].id)]
    assert str(broad["id"]) not in binding["requirements"][0]["permit_rule_ids"]
    assert bundle["modes"]["model:invoke"] == "require_permit"
    assert bundle["ancestor_require_permit"] == ["model:invoke"]
    assert "synthetic-secret" not in repr(bundle)


def test_unrelated_metadata_concurrent_writer_survives(resource: Any) -> None:
    crud, owners, ids = resource
    account, _ = owners[0]
    initial = crud.resource_access(
        account_id=account, resource_type="ai_model", resource_id=ids[0]
    )
    with crud.session() as db, db.begin():
        row = db.get(models.Account, account)
        row.meta_data = {**(row.meta_data or {}), "concurrent_setting": "preserved"}
    with pytest.raises(AccessRuleConflictError, match="preview"):
        save(resource, update(local_rule(ids[0])), generation=initial.generation)
    save(resource, update(local_rule(ids[0])))
    with crud.session() as db:
        assert (
            db.get(models.Account, account).meta_data["concurrent_setting"]
            == "preserved"
        )


def test_local_requirement_committed_invalidation_and_rollback(
    resource: Any, monkeypatch: Any
) -> None:
    import preloop.models.crud.access_rule as module

    crud, owners, ids = resource
    account, _ = owners[0]
    ready, stop, received = threading.Event(), threading.Event(), threading.Event()

    def changed(key: str, generation: int) -> None:
        if key == str(account):
            received.set()

    thread = threading.Thread(
        target=crud.listen, args=(changed, ready, stop), daemon=True
    )
    thread.start()
    original = module._record_change
    generation = crud.generation(account_id=account)

    def fail(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise RuntimeError("Synthetic snapshot failure")

    try:
        assert ready.wait(5)
        monkeypatch.setattr(module, "_record_change", fail)
        with pytest.raises(RuntimeError, match="snapshot failure"):
            save(resource, update(local_rule(ids[0])))
        assert crud.generation(account_id=account) == generation
        assert crud.rules(account_id=account) == []
        assert not received.wait(0.2)
        monkeypatch.setattr(module, "_record_change", original)
        save(resource, update(local_rule(ids[0])))
        assert received.wait(5)
    finally:
        stop.set()
        thread.join(3)
    assert not thread.is_alive()


def test_yaml_cannot_detach_local_permits_or_edit_active_allowlists(
    resource: Any,
) -> None:
    crud, owners, ids = resource
    account, user = owners[0]
    saved = save(resource, update(local_rule(ids[0])))
    with crud.session() as db:
        exported = export_current_policy(db, account)
        with pytest.raises(AccessRuleConflictError, match="detached"):
            validate_policy_section(db, account, None, None, [])
        altered = saved.rules[0].model_copy(
            update={
                "subject_selector": saved.rules[0].subject_selector.model_copy(
                    update={"kind": "user", "ids": [user]}
                )
            }
        )
        with pytest.raises(AccessRuleConflictError, match="preview"):
            validate_policy_section(
                db, account, [altered], None, exported.resource_access
            )
    with crud.session() as db, db.begin():
        apply_policy_section(db, account, user, [], None, [])
    assert crud.rules(account_id=account) == []
    cleared = crud.resource_access(
        account_id=account, resource_type="ai_model", resource_id=ids[0]
    )
    assert cleared.requirements == [] and cleared.rules == []


def test_foreign_resource_rule_ids_are_validated_in_general_crud_and_import(
    resource: Any,
) -> None:
    crud, owners, ids = resource
    account, user = owners[0]
    for rule in (local_rule(ids[1]), local_rule(ids[0], resource_type="mcp_server")):
        with pytest.raises(AccessReferenceError):
            crud.write(account_id=account, user_id=user, definition=rule)
        with crud.session() as db:
            with pytest.raises(AccessReferenceError):
                validate_policy_section(db, account, [rule], None)


def test_two_resource_documents_cannot_steal_each_others_permits(resource: Any) -> None:
    crud, owners, ids = resource
    account, user = owners[0]
    saved = save(resource, update(local_rule(ids[0])))
    with crud.session() as db, db.begin():
        other = models.AIModel(
            account_id=account,
            name="Another example",
            provider_name="example",
            model_identifier="other-example",
        )
        db.add(other)
        db.flush()
        other_id = other.id
    try:
        with pytest.raises(AccessRuleConflictError, match="already in use"):
            crud.write_resource_access(
                account_id=account,
                user_id=user,
                resource_type="ai_model",
                resource_id=other_id,
                document=update(local_rule(other_id, id=saved.rules[0].id)),
                expected_version=0,
                expected_generation=crud.generation(account_id=account),
                losing_subjects=[],
            )
        assert (
            crud.resource_access(
                account_id=account, resource_type="ai_model", resource_id=ids[0]
            ).rules
            == saved.rules
        )
    finally:
        with crud.session() as db, db.begin():
            db.delete(db.get(models.AIModel, other_id))


def test_requirement_only_write_invalidates_and_unsupported_action_rolls_back(
    resource: Any,
) -> None:
    crud, owners, ids = resource
    account, _ = owners[0]
    initial = crud.generation(account_id=account)
    saved = save(
        resource,
        ResourceAccessUpdate(
            requirements=[ResourceAccessRequirement(action="model:invoke")]
        ),
    )
    assert saved.generation > initial
    assert saved.rules == []
    assert (
        crud.bundle(account_id=account)["resource_access"][0]["requirements"][0][
            "permit_rule_ids"
        ]
        == []
    )
    with pytest.raises(AccessRuleConflictError, match="unsupported"):
        save(
            resource,
            ResourceAccessUpdate(
                requirements=[ResourceAccessRequirement(action="runner:accept")]
            ),
        )
    assert crud.generation(account_id=account) == saved.generation
    assert (
        crud.resource_access(
            account_id=account, resource_type="ai_model", resource_id=ids[0]
        )
        == saved
    )


def test_concurrent_resource_writers_only_one_generation_can_commit(
    resource: Any,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    crud, owners, ids = resource
    account, user = owners[0]
    initial = crud.resource_access(
        account_id=account, resource_type="ai_model", resource_id=ids[0]
    )
    barrier = threading.Barrier(2)

    def write() -> str:
        rule = local_rule(ids[0])
        barrier.wait(timeout=5)
        try:
            crud.write_resource_access(
                account_id=account,
                user_id=user,
                resource_type="ai_model",
                resource_id=ids[0],
                document=update(rule),
                expected_version=initial.version,
                expected_generation=initial.generation,
                losing_subjects=[],
            )
            return "committed"
        except AccessRuleConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: write(), range(2)))
    assert sorted(outcomes) == ["committed", "conflict"]
    saved = crud.resource_access(
        account_id=account, resource_type="ai_model", resource_id=ids[0]
    )
    assert saved.version == 1 and len(saved.rules) == 1
    assert saved.requirements[0].permit_rule_ids == [saved.rules[0].id]
