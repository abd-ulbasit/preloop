"""Tests for account-scoped realtime event encoding."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from preloop.services.account_realtime import encode_realtime_event_for_nats


def test_encode_realtime_event_serializes_datetime_payload() -> None:
    """Managed-agent summaries include datetime fields that must JSON-encode."""
    observed_at = datetime(2026, 6, 9, 12, 30, tzinfo=UTC)
    event = {
        "account_id": str(uuid4()),
        "topic": "managed_agents",
        "type": "managed_agent_updated",
        "timestamp": observed_at.isoformat(),
        "payload": {
            "id": str(uuid4()),
            "last_seen_at": observed_at,
            "lifecycle_updated_at": observed_at,
        },
    }

    payload_bytes = encode_realtime_event_for_nats(event, context="test account")
    assert payload_bytes is not None
    assert b"2026-06-09T12:30:00" in payload_bytes


def test_encode_realtime_event_serializes_uuid_values() -> None:
    """Nested UUID values should be converted to strings."""
    agent_id = uuid4()
    event = {
        "account_id": str(uuid4()),
        "topic": "managed_agents",
        "type": "managed_agent_updated",
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": {"agent_id": agent_id},
    }

    payload_bytes = encode_realtime_event_for_nats(event, context="test account")
    assert payload_bytes is not None
    assert str(agent_id).encode() in payload_bytes
    assert UUID(str(agent_id))


def test_oversized_gateway_event_keeps_recent_redacted_preview() -> None:
    """Long conversations still provide a bounded canvas bubble after truncation."""
    import json

    event = {
        "type": "model_gateway_call",
        "payload": {
            "managed_agent_id": "agent-1",
            "request": {"input": "x" * 1_100_000},
            "response": {"output": "raw text must not become a preview"},
            "conversation_preview": {
                "messages": [
                    {"source": "request", "role": "user", "text": "old"},
                    {"source": "request", "role": "user", "text": "recent question"},
                    {
                        "source": "response",
                        "role": "assistant",
                        "text": "[REDACTED] " + "answer " * 1000,
                        "redacted": True,
                    },
                ],
                "metadata": {"message_count": 3, "has_redacted_content": True},
            },
        },
    }
    encoded = encode_realtime_event_for_nats(event, context="test")
    assert encoded is not None
    assert len(encoded) < 10_000
    payload = json.loads(encoded)["payload"]
    assert "request" not in payload and "response" not in payload
    messages = payload["conversation_preview"]["messages"]
    assert len(messages) == 2
    assert messages[-1]["text"].startswith("[REDACTED]")
    assert len(messages[-1]["text"]) <= 300
    assert messages[-1]["redacted"] is True
    assert payload["conversation_preview"]["metadata"]["has_truncated_content"]
    assert len(event["payload"]["conversation_preview"]["messages"]) == 3


def test_oversized_event_never_builds_preview_from_uncaptured_body() -> None:
    """Size reduction must preserve the producer's content-capture decision."""
    import json

    event = {
        "payload": {
            "request": {"input": "private " * 200_000},
            "conversation_preview": {"messages": [], "metadata": {}},
        }
    }
    encoded = encode_realtime_event_for_nats(event, context="test")
    assert encoded is not None
    assert json.loads(encoded)["payload"]["conversation_preview"]["messages"] == []
