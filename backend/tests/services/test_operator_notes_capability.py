"""Which agents the console offers a note box to."""

import pytest

from preloop.services.operator_notes import agent_supports_notes


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # MCP-only agent of a kind with no note hook: nothing can carry a note.
        ({"agent_kind": "devin", "model_gateway_configured": False}, False),
        ({"agent_kind": "copilot_cli", "model_gateway_configured": False}, False),
        ({"agent_kind": None, "model_gateway_configured": False}, False),
        # Gateway traffic carries a note at the next model call.
        ({"agent_kind": "devin", "model_gateway_configured": True}, True),
        (
            {
                "agent_kind": "devin",
                "model_gateway_configured": False,
                "total_requests": 3,
            },
            True,
        ),
        # Agent Control or a harness hook can pull it.
        (
            {
                "agent_kind": "devin",
                "model_gateway_configured": False,
                "control_enabled": True,
            },
            True,
        ),
        ({"agent_kind": "claude_code", "model_gateway_configured": False}, True),
        ({"agent_kind": "Cursor", "model_gateway_configured": False}, True),
        ({"agent_kind": "opencode", "model_gateway_configured": False}, True),
    ],
)
def test_agent_supports_notes(kwargs, expected):
    kwargs.setdefault("control_enabled", False)
    assert agent_supports_notes(**kwargs) is expected
