"""RunnerRegisterRequest accepts null labels from older CLI configs (#1530)."""

from preloop.models.schemas.flow_runner import RunnerRegisterRequest


def test_null_labels_default_to_empty_list():
    req = RunnerRegisterRequest.model_validate({"name": "r", "labels": None})
    assert req.labels == []


def test_labels_preserved():
    req = RunnerRegisterRequest.model_validate({"labels": ["personal"]})
    assert req.labels == ["personal"]
