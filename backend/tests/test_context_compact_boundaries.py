from __future__ import annotations

from app.context.compact_boundaries import (
    compact_metadata,
    expand_tool_pair_message_ids,
    is_compact_boundary,
    latest_compact_boundary,
    preserved_segment_messages,
    preserved_segment_with_tool_call_owners,
    redact_compact_metadata,
    retained_tail_message_ids,
)


def test_compact_boundary_detection_accepts_metadata_variants():
    messages = [
        {"id": "old", "metadata": {"context_boundary": "manual_compact"}},
        {"id": "new", "metadata": {"compactMetadata": {"type": "reactive_compact"}}},
    ]

    assert is_compact_boundary(messages[0]) is True
    assert is_compact_boundary(messages[1]) is True
    assert latest_compact_boundary(messages)["id"] == "new"


def test_retained_tail_message_ids_collects_supported_metadata_shapes():
    boundary = {
        "metadata": {
            "retained_tail_message_ids": ["m1", ""],
            "compact_metadata": {
                "messages_to_keep_ids": ["m2"],
                "preserved_segment": {"message_ids": ["m3"]},
            },
        }
    }

    assert retained_tail_message_ids(boundary) == {"m1", "m2", "m3"}


def test_preserved_segment_messages_are_copied_and_redacted_for_public_metadata():
    boundary = {
        "metadata": {
            "compact_metadata": {
                "preserved_segment": {
                    "messages": [
                        {"id": "m1", "role": "assistant", "content": "keep"},
                        "not a message",
                    ]
                }
            }
        }
    }

    messages = preserved_segment_messages(boundary)
    messages[0]["content"] = "mutated"

    assert preserved_segment_messages(boundary)[0]["content"] == "keep"
    assert compact_metadata(boundary)["preserved_segment"]["messages"][0]["content"] == "keep"
    assert redact_compact_metadata(compact_metadata(boundary))["preserved_segment"] == {"message_count": 1}


def test_preserved_segment_includes_missing_tool_call_owner_once():
    prior_messages = [
        {
            "id": "assistant_owner",
            "role": "assistant",
            "tool_calls": [{"id": "call_1", "function": {"name": "file.read"}}],
        }
    ]
    preserved_segment = [{"id": "tool_result", "role": "tool", "tool_call_id": "call_1", "content": "result"}]

    result = preserved_segment_with_tool_call_owners(prior_messages, preserved_segment)

    assert [message["id"] for message in result] == ["assistant_owner", "tool_result"]


def test_expand_tool_pair_message_ids_keeps_assistant_and_tool_result_together():
    messages = [
        {"id": "assistant_owner", "role": "assistant", "tool_calls": [{"id": "call_1"}]},
        {"id": "tool_result", "role": "tool", "tool_call_id": "call_1"},
    ]

    assert expand_tool_pair_message_ids(messages, {"tool_result"}) == {"assistant_owner", "tool_result"}
    assert expand_tool_pair_message_ids(messages, {"assistant_owner"}) == {"assistant_owner", "tool_result"}
