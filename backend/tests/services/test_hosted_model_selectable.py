"""Hosted models with no verified operator tariff are hidden from pickers (#807)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

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
