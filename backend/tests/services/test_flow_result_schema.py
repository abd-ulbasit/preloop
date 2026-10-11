"""Tests for the per-flow result schema helpers (#868)."""

from types import SimpleNamespace

import pytest

from preloop.services.flow_failure_category import derive_failure_category
from preloop.services.flow_result_schema import (
    RESULT_SCHEMA_MAX_BYTES,
    RESULT_SCHEMA_MAX_ERRORS,
    ResultSchemaError,
    check_result_schema,
    flow_result_schema,
    schema_failure_message,
    validate_result_document,
)

VERDICT_SCHEMA = {
    "type": "object",
    "required": ["status", "verdict"],
    "properties": {"status": {"type": "string"}, "verdict": {"type": "string"}},
}


def test_missing_required_field_reports_its_path():
    result = validate_result_document({"status": "success"}, VERDICT_SCHEMA)
    assert result["valid"] is False
    assert result["errors"] == [
        {"path": "$.verdict", "message": "'verdict' is a required property"}
    ]
    assert result["error_count"] == 1


def test_conforming_document_is_valid():
    result = validate_result_document(
        {"status": "success", "verdict": "PASS"}, VERDICT_SCHEMA
    )
    assert result == {"valid": True, "errors": [], "error_count": 0}


def test_nested_path_and_container_termination_ignored():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "analysis": {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "integer"}}
                },
            }
        },
    }
    doc = {"analysis": {"items": [1, "x"]}, "container_termination": {"code": 0}}
    result = validate_result_document(doc, schema)
    assert result["errors"][0]["path"] == "$.analysis.items[1]"
    assert result["error_count"] == 1


def test_missing_document_is_invalid():
    result = validate_result_document(None, VERDICT_SCHEMA)
    assert result["valid"] is False
    assert result["errors"][0]["path"] == "$"


def test_errors_are_bounded():
    schema = {
        "type": "object",
        "required": [f"k{i}" for i in range(RESULT_SCHEMA_MAX_ERRORS + 5)],
    }
    result = validate_result_document({}, schema)
    assert len(result["errors"]) == RESULT_SCHEMA_MAX_ERRORS
    assert result["error_count"] == RESULT_SCHEMA_MAX_ERRORS + 5


def test_check_rejects_invalid_schema_readably():
    with pytest.raises(ResultSchemaError, match="not a valid JSON Schema"):
        check_result_schema({"type": "nonsense"})


def test_check_rejects_oversized_schema():
    big = {"description": "x" * (RESULT_SCHEMA_MAX_BYTES + 1)}
    with pytest.raises(ResultSchemaError, match="limit is"):
        check_result_schema(big)


def test_check_rejects_non_object_and_accepts_none():
    with pytest.raises(ResultSchemaError):
        check_result_schema(["type"])
    assert check_result_schema(None) is None
    assert check_result_schema(VERDICT_SCHEMA) == VERDICT_SCHEMA


def test_flow_result_schema_respects_toggle():
    assert flow_result_schema(None) is None
    assert flow_result_schema(SimpleNamespace(result_schema=None)) is None
    on = SimpleNamespace(result_schema=VERDICT_SCHEMA, validate_result=True)
    off = SimpleNamespace(result_schema=VERDICT_SCHEMA, validate_result=False)
    assert flow_result_schema(on) == VERDICT_SCHEMA
    assert flow_result_schema(off) is None


def test_failure_message_maps_to_category():
    message = schema_failure_message(
        validate_result_document({"status": "success"}, VERDICT_SCHEMA)
    )
    assert "$.verdict" in message
    assert (
        derive_failure_category(status="FAILED", error_message=message)
        == "result_schema_invalid"
    )


def test_flow_create_and_update_reject_bad_schema_readably():
    from pydantic import ValidationError

    from preloop.models.schemas.flow import FlowCreate, FlowUpdate

    base = {
        "name": "analysis",
        "prompt_template": "Answer one question.",
        "agent_config": {},
    }
    created = FlowCreate(**base, result_schema=VERDICT_SCHEMA)
    assert created.result_schema == VERDICT_SCHEMA
    assert created.validate_result is True
    assert FlowCreate(**base).result_schema is None

    with pytest.raises(ValidationError, match="not a valid JSON Schema"):
        FlowUpdate(result_schema={"type": 5})
    with pytest.raises(ValidationError, match="limit is"):
        FlowUpdate(result_schema={"description": "x" * (RESULT_SCHEMA_MAX_BYTES + 1)})
    update = FlowUpdate(result_schema=None, validate_result=False)
    assert update.model_dump(exclude_unset=True) == {
        "result_schema": None,
        "validate_result": False,
    }


def test_flow_response_does_not_revalidate_a_stored_schema():
    """Reads trust the schema checked at save (review on #1539)."""
    import preloop.models.schemas.flow as flow_schemas

    def checks_schema(model) -> bool:
        validators = model.__pydantic_decorators__.field_validators.values()
        return any("result_schema" in v.info.fields for v in validators)

    assert not checks_schema(flow_schemas.FlowResponse)
    assert checks_schema(flow_schemas.FlowCreate)
    assert checks_schema(flow_schemas.FlowUpdate)
