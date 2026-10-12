"""Hosted models with no verified operator tariff are hidden from pickers (#807)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from preloop.api.endpoints.ai_models import _apply_tariff_visibility
from preloop.models import models
from preloop.schemas.ai_model import AIModelRead
from preloop.services.hosted_spend_guard import hosted_model_selectable

_PM = "preloop.services.hosted_spend_guard.get_plugin_manager"


def _hosted(verified: bool) -> models.AIModel:
    return models.AIModel(
        id=uuid4(),
        name="Hosted Flash",
        account_id=None,
        provider_name="google",
        model_identifier="flash",
        is_default=False,
        updated_at=datetime.now(timezone.utc),
        meta_data={
            "gateway": {"enabled": True},
            "hosted_metering": {"text_context_bound_verified": verified},
        },
    )


def _byok() -> models.AIModel:
    model = _hosted(False)
    model.account_id = uuid4()
    model.meta_data = {}
    return model


def _meter():
    meter = MagicMock()
    meter.selectable.side_effect = lambda m: bool(
        m.meta_data["hosted_metering"]["text_context_bound_verified"]
    )
    return meter


def _with_meter(meter):
    pm = MagicMock()
    pm.get_service.return_value = meter
    return patch(_PM, return_value=pm)


def test_no_plugin_means_everything_selectable() -> None:
    with _with_meter(None):
        assert hosted_model_selectable(_hosted(False))


def test_plugin_without_hook_keeps_models_selectable() -> None:
    with _with_meter(object()):
        assert hosted_model_selectable(_hosted(False))


def test_plugin_hook_decides_for_hosted_rows_only() -> None:
    meter = _meter()
    with _with_meter(meter):
        assert not hosted_model_selectable(_hosted(False))
        assert hosted_model_selectable(_hosted(True))
        meter.selectable.reset_mock()
        assert hosted_model_selectable(_byok())
        meter.selectable.assert_not_called()


def test_non_admin_list_drops_unpriced_hosted_rows() -> None:
    priced, unpriced, byok = _hosted(True), _hosted(False), _byok()
    with _with_meter(_meter()):
        rows = _apply_tariff_visibility([priced, unpriced, byok], is_admin=False)
    assert rows == [priced, byok]


def test_admin_list_keeps_unpriced_rows_marked() -> None:
    priced, unpriced = _hosted(True), _hosted(False)
    with _with_meter(_meter()):
        rows = _apply_tariff_visibility([priced, unpriced], is_admin=True)
    assert rows[0] is priced
    assert isinstance(rows[1], AIModelRead)
    assert rows[1].unpriced is True
    assert rows[1].id == unpriced.id
    assert AIModelRead.model_validate(priced).unpriced is False


def test_hook_failure_fails_open() -> None:
    meter = MagicMock()
    meter.selectable.side_effect = RuntimeError("ledger down")
    with _with_meter(meter):
        assert hosted_model_selectable(_hosted(False))


@pytest.mark.parametrize("superuser", [False, True])
def test_list_endpoint_applies_tariff_visibility(superuser: bool, mocker) -> None:
    from fastapi.testclient import TestClient

    from preloop.api.app import create_app
    from preloop.api.auth import get_current_active_user
    from preloop.models.db.session import get_db_session

    priced, unpriced = _hosted(True), _hosted(False)
    priced.name, unpriced.name = "Priced", "Unpriced"
    for m in (priced, unpriced):
        m.created_at = m.updated_at
    crud = mocker.patch("preloop.api.endpoints.ai_models.crud_ai_model")
    crud.get_by_account.return_value = [priced, unpriced]
    mocker.patch(
        "preloop.models.crud.resource_share.crud_resource_share.public_list",
        return_value=[],
    )
    user = SimpleNamespace(
        id=uuid4(), account_id=uuid4(), is_superuser=superuser, permissions=None
    )
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: MagicMock()
    app.dependency_overrides[get_current_active_user] = lambda: user
    with _with_meter(_meter()):
        res = TestClient(app).get("/api/v1/ai-models")
    assert res.status_code == 200, res.text
    rows = {r["name"]: r["unpriced"] for r in res.json()}
    if superuser:
        assert rows == {"Priced": False, "Unpriced": True}
    else:
        assert rows == {"Priced": False}
