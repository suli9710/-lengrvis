from __future__ import annotations

from app.context.fallback_trim import fallback_target_tokens, trim_oldest_unprotected_blocks


def test_fallback_target_tokens_reserves_manual_compact_buffer():
    assert fallback_target_tokens(1000, 250) == 750
    assert fallback_target_tokens(1000, -5) == 1000
    assert fallback_target_tokens(1, 250) == 1


def test_trim_oldest_unprotected_blocks_preserves_system_latest_user_boundary_and_tool_pair():
    messages = [
        {"id": "system", "role": "system", "content": "system prompt"},
        {"id": "old_user", "role": "user", "content": "old user " + "x" * 200},
        {"id": "old_assistant", "role": "assistant", "content": "old assistant " + "x" * 200},
        {
            "id": "boundary",
            "role": "system",
            "content": "compacted",
            "metadata": {"context_boundary": "reactive_compact"},
        },
        {"id": "middle", "role": "assistant", "content": "middle " + "x" * 200},
        {
            "id": "tool_owner",
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{}"}}],
        },
        {"id": "tool_result", "role": "tool", "content": "tool result " + "x" * 200, "tool_call_id": "call_1"},
        {"id": "latest_user", "role": "user", "content": "latest user"},
    ]

    trimmed = trim_oldest_unprotected_blocks(messages, target_tokens=60)
    trimmed_ids = [message["id"] for message in trimmed]

    assert trimmed_ids == ["system", "boundary", "tool_owner", "tool_result", "latest_user"]
    assert [message["id"] for message in messages] == [
        "system",
        "old_user",
        "old_assistant",
        "boundary",
        "middle",
        "tool_owner",
        "tool_result",
        "latest_user",
    ]


def test_trim_oldest_unprotected_blocks_breaks_when_everything_is_protected():
    messages = [
        {"id": "system", "role": "system", "content": "x" * 200},
        {"id": "latest_user", "role": "user", "content": "x" * 200},
    ]

    trimmed = trim_oldest_unprotected_blocks(messages, target_tokens=1)

    assert [message["id"] for message in trimmed] == ["system", "latest_user"]
    assert trimmed is not messages
