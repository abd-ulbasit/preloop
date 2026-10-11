"""Messages between a tool call and its result (preloop/preloop#1557).

Codex stores a ``function_call`` when the model emits it and stores its output
only after the tool finishes. Developer/user messages recorded in between (a
PreToolUse hook's additional context, a steer, a model/permissions switch)
end up between the two. The Responses API accepts this, so the gateway must
too. The #1113 cross-chat rule and the missing-output rule are unchanged.
"""

from __future__ import annotations

import pytest

from preloop.services import codex_upstream_ws as cw
from preloop.services.model_gateway_errors import ModelGatewayAPIError

from tests.services.test_codex_crosschat import DELEGATION, _item, _normalize
from tests.services.test_codex_upstream_ws import _chained_entry, _payload

SECRET = "TOP-SECRET-DEVELOPER-CONTENT"


def _user(text):
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }


def _developer(text, **extra):
    return {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": text}],
        **extra,
    }


def _call(call_id="call_1"):
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": "exec_command",
        "arguments": "{}",
    }


def _output(call_id="call_1", output="ok"):
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _assert_tool_pairing(messages):
    """Every assistant tool_calls message is followed by its tool messages."""
    for i, message in enumerate(messages):
        if message.get("tool_calls"):
            ids = {tc["id"] for tc in message["tool_calls"]}
            following = messages[i + 1 : i + 1 + len(ids)]
            assert {m.get("tool_call_id") for m in following} == ids
            assert all(m["role"] == "tool" for m in following)


@pytest.mark.parametrize("role", ["developer", "user"])
def test_message_between_call_and_output_is_kept_and_pairs(role):
    between = {
        "type": "message",
        "role": role,
        "content": [{"type": "input_text", "text": "context changed"}],
    }
    messages = _normalize([_user("go"), _call(), between, _output(), _user("next")])
    _assert_tool_pairing(messages)
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "tool",
        role,
        "user",
    ]
    assert messages[2]["tool_call_id"] == "call_1"
    assert messages[3]["content"] == "context changed"


def test_messages_between_parallel_calls_keep_order_after_all_results():
    messages = _normalize(
        [
            _user("go"),
            _call("call_a"),
            _call("call_b"),
            _developer("first"),
            _output("call_a"),
            _user("second"),
            _output("call_b"),
        ]
    )
    _assert_tool_pairing(messages)
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "developer",
        "user",
    ]
    assert [messages[4]["content"], messages[5]["content"]] == ["first", "second"]


def test_aborted_output_after_interleaved_developer_message():
    """Interrupt path: Codex records the synthesized aborted output last."""
    messages = _normalize(
        [
            _call(),
            _developer("<permissions instructions>"),
            _output(output="aborted by user after 1.2s"),
        ]
    )
    _assert_tool_pairing(messages)
    assert messages[-1]["role"] == "developer"


def test_prod_2026_10_11_developer_message_between_call_and_output():
    """Exact prod shape from the 2026-10-11 21:22Z 400s: developer message with
    an ``id`` and no ``call_id`` between a function_call and its output."""
    call_id = "call_d0807ca3c9cd44aca06bf9362b8b62d1"
    history = [_user(f"turn {n}") for n in range(154 - 1)]
    items = history + [
        _call(call_id),
        _developer(SECRET, id="msg_01a12cd8-5edb-77e3-a32c-8ae37360b24b"),
        _output(call_id),
    ]
    messages = _normalize(items)
    _assert_tool_pairing(messages)
    assert messages[-1] == {"role": "developer", "content": SECRET}


def test_missing_output_still_400s_without_content():
    with pytest.raises(ModelGatewayAPIError) as caught:
        _normalize([_user("go"), _call(), _developer(SECRET)])
    assert caught.value.status_code == 400
    assert "call_1" in caught.value.message
    assert "tool calls without results" in caught.value.message
    assert SECRET not in caught.value.message


def test_crosschat_delivery_between_call_and_output_still_400s():
    with pytest.raises(ModelGatewayAPIError) as caught:
        _normalize([_call(), _developer("ctx"), _item(output=DELEGATION), _output()])
    message = caught.value.message
    assert caught.value.status_code == 400
    assert "index=2" in message
    assert "Codex delivery between a tool call and its result" in message
    assert DELEGATION not in message


def test_non_message_item_between_call_and_output_still_400s_naming_role():
    reasoning = {"type": "reasoning", "summary": [], "id": "rs_1"}
    with pytest.raises(ModelGatewayAPIError) as caught:
        _normalize([_call(), reasoning, _output()])
    assert "index=1 type='reasoning'" in caught.value.message
    assert "item between a tool call and its result" in caught.value.message


# --- Codex upstream WS diff ------------------------------------------------


def test_ws_developer_message_appended_after_call_is_a_clean_increment():
    """Codex's own shape: the previous response ended with the call, the new
    input appends [developer, output]. The prefix matches, so the delta is
    sent as a normal incremental frame."""
    first = [_user("go")]
    entry = _chained_entry(first, [_call()])
    current = first + [_call(), _developer("ctx"), _output()]
    plan = cw.plan_request(entry, _payload(current))
    assert plan.mode == "incremental"
    assert plan.items == [_developer("ctx"), _output()]


def test_ws_developer_message_inserted_inside_chain_forces_full_resend():
    first = [_user("go")]
    entry = _chained_entry(first, [_call(), _call("call_2")])
    current = first + [_call(), _developer("ctx"), _call("call_2"), _output()]
    plan = cw.plan_request(entry, _payload(current))
    assert (plan.mode, plan.reason) == ("full", "input_mismatch")
    assert plan.previous_response_id is None
    assert plan.items == current


def test_message_between_custom_tool_call_and_output_is_kept_and_pairs():
    """Codex replays freeform calls as ``custom_tool_call`` on every turn."""
    call = {
        "type": "custom_tool_call",
        "call_id": "call_c",
        "name": "apply_patch",
        "input": "*** Begin Patch",
    }
    output = {"type": "custom_tool_call_output", "call_id": "call_c", "output": "ok"}
    messages = _normalize([_user("go"), call, _developer("ctx"), output])
    _assert_tool_pairing(messages)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "developer"]
    assert messages[2]["tool_call_id"] == "call_c"
