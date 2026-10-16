"""Ordinary GitLab flows must not inherit optional product-audit URL rules."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from preloop.services.flow_orchestrator import FlowExecutionOrchestrator
from preloop.services.product_provenance import RuntimeProvenanceFacts
from preloop.services.trusted_publisher import PublicationError


@pytest.mark.parametrize("require_observed", [False, True])
def test_unmapped_http_flow_skips_product_facts(require_observed: bool) -> None:
    """Both preparation and finalization leave ordinary HTTP checkouts alone."""
    orchestrator = FlowExecutionOrchestrator.__new__(FlowExecutionOrchestrator)
    orchestrator.flow = SimpleNamespace(
        git_clone_config={
            "repositories": [
                {"repository_url": "http://host.docker.internal:53080/example/app.git"}
            ]
        }
    )
    orchestrator.trigger_event_data = {"payload": {"object_kind": "merge_request"}}
    facts = Mock(wraps=orchestrator._product_runtime_facts)
    orchestrator._product_runtime_facts = facts

    assert (
        orchestrator._verify_product_provenance_record(
            require_observed=require_observed
        )
        is None
    )
    facts.assert_not_called()


def test_explicit_product_mapping_still_validated() -> None:
    """Opting in still rejects an invalid mapping before any publication."""
    orchestrator = FlowExecutionOrchestrator.__new__(FlowExecutionOrchestrator)
    orchestrator.flow = SimpleNamespace()
    orchestrator.trigger_event_data = {"payload": {"product_provenance": {}}}
    orchestrator._product_runtime_facts = Mock(
        return_value=RuntimeProvenanceFacts((), (), {}, None, None)
    )

    with pytest.raises(PublicationError, match="product"):
        orchestrator._verify_product_provenance_record()
