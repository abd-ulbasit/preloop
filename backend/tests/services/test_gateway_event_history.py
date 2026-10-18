"""history=delta on session gateway events (#1586)."""

from __future__ import annotations

import copy

from preloop.services.gateway_event_history import elide_replayed_history


def _event(index: int, history: list[dict], response: str, tools: list[dict]):
    return {
        "id": f"event-{index}",
        "timestamp": f"2026-10-13T10:{index:02d}:00+00:00",
        "type": "model_gateway_call",
        "payload": {
            "conversation_preview": {
                "messages": [
                    *history,
                    {"role": "assistant", "source": "response", "text": response},
                ],
                "metadata": {"message_count": len(history) + 1},
            },
            "tools": tools,
        },
    }


def _session(calls: int) -> list[dict]:
    """Newest first, like the endpoint: each call replays all earlier ones."""
    system = {"role": "system", "source": "request", "text": "You are an agent."}
    history = [system, {"role": "user", "source": "request", "text": "Fix the test"}]
    tools: list[dict] = []
    events = []
    for index in range(calls):
        response = f"step {index}"
        tools = [
            *tools,
            {"kind": "call", "call_id": f"c{index}", "name": "bash", "text": "ls"},
            {"kind": "result", "call_id": f"c{index}", "name": "bash", "text": "ok"},
        ]
        events.append(_event(index, history, response, list(tools)))
        history = [
            *history,
            {"role": "assistant", "source": "request", "text": response},
            {"role": "tool", "source": "request", "text": f"result {index}"},
        ]
    return list(reversed(events))


def _texts(event: dict) -> list[tuple[int, str]]:
    return [
        (message["position"], message["text"])
        for message in event["payload"]["conversation_preview"]["messages"]
    ]


def test_oldest_event_keeps_everything_newer_ones_only_what_is_new():
    events = _session(3)
    result = elide_replayed_history(events)

    oldest, middle, newest = result[2], result[1], result[0]
    assert _texts(oldest) == [
        (0, "You are an agent."),
        (1, "Fix the test"),
        (2, "step 0"),
    ]
    # The replayed system prompt and the history are gone; the user request
    # the per-event views title by stays; positions are the original ones.
    assert _texts(middle) == [(1, "Fix the test"), (3, "result 0"), (4, "step 1")]
    assert _texts(newest) == [
        (1, "Fix the test"),
        (5, "result 1"),
        (6, "step 2"),
    ]
    assert (
        newest["payload"]["conversation_preview"]["metadata"]["history_elided_count"]
        == 4
    )


def test_tools_already_carried_are_dropped():
    result = elide_replayed_history(_session(3))
    assert [entry["call_id"] for entry in result[0]["payload"]["tools"]] == [
        "c2",
        "c2",
    ]
    assert len(result[2]["payload"]["tools"]) == 2


def test_input_is_not_mutated_and_order_is_kept():
    events = _session(4)
    before = copy.deepcopy(events)
    result = elide_replayed_history(events)
    assert events == before
    assert [event["id"] for event in result] == [event["id"] for event in events]


def test_repeated_text_at_a_new_position_is_kept():
    first = _event(
        0, [{"role": "user", "source": "request", "text": "continue"}], "a", []
    )
    second = _event(
        1,
        [
            {"role": "user", "source": "request", "text": "continue"},
            {"role": "assistant", "source": "request", "text": "a"},
            {"role": "user", "source": "request", "text": "continue"},
        ],
        "b",
        [],
    )
    result = elide_replayed_history([second, first])
    assert _texts(result[0]) == [(2, "continue"), (3, "b")]


def test_events_without_preview_pass_through():
    event = {
        "id": "x",
        "timestamp": None,
        "type": "model_gateway_call",
        "payload": None,
    }
    assert elide_replayed_history([event]) == [event]


def test_window_bounds_the_oldest_event_to_its_last_messages():
    """#1689: a page on a long session must not carry the whole history."""
    events = _session(40)
    result = elide_replayed_history(events[:20], window=10)
    oldest = result[-1]
    preview = oldest["payload"]["conversation_preview"]
    positions = [message["position"] for message in preview["messages"]]
    total = len(events[19]["payload"]["conversation_preview"]["messages"])
    # The last user message is kept as the event's title even when old.
    tail = [p for p in positions if p >= total - 10]
    assert tail == list(range(total - 10, total))
    assert len(positions) <= 11
    assert preview["metadata"]["history_window_start"] == total - 10
    # The oldest event has nothing replayed from an older one; every message
    # it lost was dropped by the window, and is counted as such.
    assert preview["metadata"]["history_elided_count"] == 0
    assert preview["metadata"]["history_window_elided_count"] == total - len(positions)
    assert len(oldest["payload"]["tools"]) == 10


def test_window_keeps_newer_events_new_messages():
    events = _session(5)
    windowed = elide_replayed_history(events, window=10)
    plain = elide_replayed_history(events)
    assert [_texts(e) for e in windowed[:-1]] == [_texts(e) for e in plain[:-1]]


def test_no_window_leaves_the_oldest_event_whole():
    events = _session(10)
    result = elide_replayed_history(events)
    preview = result[-1]["payload"]["conversation_preview"]
    assert "history_window_start" not in preview["metadata"]
