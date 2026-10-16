"""Preview and import must agree on omitted versus explicit model I/O sections."""

import functools
import inspect
import io
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from fastapi import HTTPException, UploadFile

from preloop.api.endpoints import policies
from preloop.services.policy import (
    ModelIORule,
    PolicyApplier,
    PolicyDocument,
    PolicyMetadata,
    compute_policy_diff,
)


def rule(rule_id: str, enabled: bool = True) -> ModelIORule:
    """Build a harmless synthetic model request rule."""
    return ModelIORule(
        id=rule_id,
        target="model.request",
        enabled=enabled,
        conditions=[
            {"expression": "true", "condition_type": "cel", "action": "notify"}
        ],
    )


def document(rules: list[ModelIORule] | None) -> PolicyDocument:
    """Use matching metadata so only model rule changes are compared."""
    return PolicyDocument(
        metadata=PolicyMetadata(name="Synthetic policy"), model_io=rules
    )


@pytest.mark.parametrize("incoming", [None, []])
def test_omitted_and_explicit_empty_preview_are_distinct(
    incoming: list[ModelIORule] | None,
) -> None:
    """Omission preserves rules; an explicit empty section previews removal."""
    diff = compute_policy_diff(document([rule("notice")]), document(incoming))
    assert [(item.path, item.operation) for item in diff.changes] == (
        [] if incoming is None else [("$.model_io[id=notice]", "remove")]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "section,expected",
    [
        ("", ["notice", "second"]),
        ("model_io: null\n", ["notice", "second"]),
        ("model_io: []\n", []),
        (
            "model_io:\n  - id: notice\n    target: model.request\n    enabled: false\n    conditions: [{expression: 'true', condition_type: cel, action: notify}]\n  - id: new-rule\n    target: model.response\n    conditions: [{expression: 'true', condition_type: cel, action: notify}]\n",
            ["notice", "new-rule"],
        ),
    ],
)
async def test_preview_matches_import_and_scopes_state_to_current_account(
    section: str,
    expected: list[str],
    mocker: MockerFixture,
) -> None:
    """Exercise real endpoint parsing, diff and applier with tenant-scoped storage."""
    account_id, other_account_id = uuid4(), uuid4()
    account = SimpleNamespace(id=account_id)
    user = SimpleNamespace(id=uuid4(), account_id=account_id)
    db = MagicMock()
    stored = {
        str(account_id): [rule("notice"), rule("second")],
        str(other_account_id): [rule("other-account")],
    }
    before = document(stored[str(account_id)])
    exported = mocker.patch(
        "preloop.api.endpoints.policies.export_current_policy", return_value=before
    )
    mocker.patch.object(PolicyApplier, "_validate_references", return_value=[])
    mocker.patch("preloop.api.endpoints.policies._audit_policy_change")
    allowed = mocker.patch("preloop.utils.permissions.ensure_permission_in_oss")

    def replace(
        _db: MagicMock, target_account_id: str, rules: list[ModelIORule]
    ) -> None:
        stored[str(target_account_id)] = rules

    replace_rules = mocker.patch(
        "preloop.services.model_content_policy.replace_model_io_rules",
        side_effect=replace,
    )
    content = f'version: "1.0"\nmetadata:\n  name: Synthetic policy\n{section}'

    def upload() -> UploadFile:
        return UploadFile(file=io.BytesIO(content.encode()), filename="policy.yaml")

    preview = await policies.diff_policy(
        file=upload(), account=account, current_user=user, db=db
    )
    exported.assert_called_once_with(
        db, account_id=account_id, policy_name="Current Configuration"
    )
    result = await policies.upload_policy(
        file=upload(),
        dry_run=False,
        resolve_env=False,
        skip_missing_servers=False,
        account=account,
        current_user=user,
        db=db,
    )
    assert result.success
    allowed.assert_called_once_with(db, user, "manage_policies")
    after = document(stored[str(account_id)])
    assert preview.model_dump() == compute_policy_diff(before, after).model_dump()
    assert [item.id for item in stored[str(account_id)]] == expected
    assert [item.id for item in stored[str(other_account_id)]] == ["other-account"]
    if "model_io: []" in section:
        assert replace_rules.call_args.args == (db, str(account_id), [])
    if "enabled: false" in section:
        assert stored[str(account_id)][0].enabled is False
    if not section or "null" in section:
        replace_rules.assert_not_called()


@pytest.mark.asyncio
async def test_upload_requires_manage_permission_before_mutation(
    mocker: MockerFixture,
) -> None:
    """A readable preview does not grant permission to apply it."""
    mocker.patch(
        "preloop.utils.permissions.ensure_permission_in_oss",
        side_effect=HTTPException(403, "Permission denied"),
    )
    applier = mocker.patch("preloop.api.endpoints.policies.PolicyApplier")
    with pytest.raises(HTTPException) as error:
        await policies.upload_policy(
            file=UploadFile(file=io.BytesIO(b"model_io: []"), filename="policy.yaml"),
            account=SimpleNamespace(id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            db=MagicMock(),
        )
    assert error.value.status_code == 403
    applier.assert_not_called()


@pytest.mark.asyncio
async def test_preview_requires_read_permission_before_export(
    mocker: MockerFixture,
) -> None:
    """The existing read gate still protects current-account rule contents."""
    import preloop.utils.permissions as permissions

    def denied(
        required_permission: str,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        assert required_permission == "view_policies"

        def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                raise HTTPException(403, "Permission denied")

            return wrapper

        return decorate

    mocker.patch.object(permissions, "_plugin_require_permission", denied)
    mocker.patch.object(permissions, "_rbac_checks_enabled", return_value=True)
    export = mocker.patch("preloop.api.endpoints.policies.export_current_policy")
    wrapped = permissions.require_permission("view_policies")(
        inspect.unwrap(policies.diff_policy)
    )
    with pytest.raises(HTTPException) as error:
        await wrapped(
            file=UploadFile(file=io.BytesIO(b"model_io: []"), filename="policy.yaml"),
            account=SimpleNamespace(id=uuid4()),
            current_user=SimpleNamespace(id=uuid4()),
            db=MagicMock(),
        )
    assert error.value.status_code == 403
    export.assert_not_called()
