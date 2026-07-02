from __future__ import annotations

from app.config import AppSettings
from app.context.micro_compact import micro_compact_messages_with_metadata
from app.context_management import project_messages_for_llm


def _settings(**overrides) -> AppSettings:
    settings = AppSettings(
        model_context_window=2000,
        model_auto_compact_token_limit=600,
        max_tokens=200,
        context_micro_compact_age=1,
        context_micro_compact_tool_result_chars=240,
        context_history_snip_enabled=False,
        context_auto_compact_enabled=False,
        context_session_memory_enabled=False,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_tool_result_collapse_records_message_and_projection_metadata():
    tool_output = "pytest output line\n" * 80
    messages = [
        {"id": "u1", "role": "user", "content": "run tests"},
        {
            "id": "a1",
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_shell",
                    "type": "function",
                    "function": {
                        "name": "terminal.run",
                        "arguments": '{"command": "pytest backend/tests/test_context_micro_compact.py"}',
                    },
                }
            ],
        },
        {"id": "t1", "role": "tool", "tool_call_id": "call_shell", "content": tool_output},
        {"id": "recent", "role": "user", "content": "latest message stays raw"},
    ]

    projection = project_messages_for_llm(messages, _settings(), source="test")

    tool_message = next(message for message in projection.messages if message.get("id") == "t1")
    metadata = tool_message["metadata"]
    assert projection.micro_compacted is True
    assert tool_message["content"].startswith("[Tool result collapsed for projection]")
    assert "command: pytest backend/tests/test_context_micro_compact.py" in tool_message["content"]
    assert metadata["micro_compacted"] is True
    assert metadata["compacted_tool_id"] == "call_shell"
    assert metadata["collapse_kind"] == "bash"
    assert metadata["tool_name"] == "terminal.run"
    assert metadata["original_chars"] == len(tool_output)
    assert metadata["original_tokens"] > metadata["projected_tokens"]
    assert metadata["tokens_saved"] == metadata["original_tokens"] - metadata["projected_tokens"]

    compact_metadata = projection.compact_metadata["micro_compact"]
    assert compact_metadata["tokens_saved"] == metadata["tokens_saved"]
    assert compact_metadata["compacted_tool_ids"] == ["call_shell"]
    assert compact_metadata["collapsed_tool_results"] == [
        {
            "message_id": "t1",
            "tool_call_id": "call_shell",
            "tool_name": "terminal.run",
            "kind": "bash",
            "original_chars": len(tool_output),
            "projected_chars": len(tool_message["content"]),
            "tokens_saved": metadata["tokens_saved"],
        }
    ]
    assert messages[2]["content"] == tool_output


def test_attachment_block_clearing_handles_single_attachment_block_in_projection_only():
    attachment = {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
    messages = [
        {"id": "old_image", "role": "user", "content": attachment},
        {"id": "recent", "role": "assistant", "content": "ok"},
    ]

    projection = project_messages_for_llm(messages, _settings(), source="test")

    projected = next(message for message in projection.messages if message.get("id") == "old_image")
    attachment_id = "old_image:attachment:0"
    assert projected["content"] == f"[image_url attachment cleared from projection: {attachment_id}]"
    assert projected["metadata"]["micro_compacted"] is True
    assert projected["metadata"]["cleared_attachment_ids"] == [attachment_id]
    assert projection.compact_metadata["cleared_attachment_ids"] == [attachment_id]
    assert projection.compact_metadata["micro_compact"]["cleared_attachments"] == [
        {"message_id": "old_image", "attachment_id": attachment_id}
    ]
    assert messages[0]["content"] == attachment


def test_micro_compact_metadata_merges_boundary_tokens_saved_and_ids():
    tool_output = "search hit\n" * 100
    boundary = {
        "id": "boundary",
        "role": "system",
        "content": "manual compact summary",
        "metadata": {
            "context_boundary": "manual_compact",
            "compact_metadata": {
                "type": "manual_compact",
                "tokens_saved": 7,
                "compacted_tool_ids": ["call_prior"],
                "cleared_attachment_ids": ["att_prior"],
            },
        },
    }
    messages = [
        boundary,
        {
            "id": "a1",
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_search",
                    "type": "function",
                    "function": {"name": "search.query", "arguments": {"query": "context micro compact"}},
                }
            ],
        },
        {"id": "t1", "role": "tool", "tool_call_id": "call_search", "content": tool_output},
        {
            "id": "u_image",
            "role": "user",
            "content": [
                {"type": "text", "text": "older attachment"},
                {"type": "image_url", "id": "att_new", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        },
        {"id": "recent", "role": "user", "content": "keep latest"},
    ]

    projection = project_messages_for_llm(messages, _settings(), source="test")

    metadata = projection.compact_metadata
    micro_metadata = metadata["micro_compact"]
    assert metadata["type"] == "manual_compact"
    assert metadata["tokens_saved"] == 7 + micro_metadata["tokens_saved"]
    assert metadata["compacted_tool_ids"] == ["call_prior", "call_search"]
    assert metadata["cleared_attachment_ids"] == ["att_prior", "att_new"]
    assert micro_metadata["compacted_tool_ids"] == ["call_search"]
    assert micro_metadata["cleared_attachment_ids"] == ["att_new"]
    assert micro_metadata["tokens_saved"] > 0


def test_deep_module_returns_ids_and_tokens_saved_metadata():
    messages = [
        {
            "id": "u_audio",
            "role": "user",
            "content": {"type": "input_audio", "id": "audio_1", "input_audio": {"data": "abc"}},
        },
        {"id": "recent", "role": "assistant", "content": "ok"},
    ]

    compacted, changed, metadata = micro_compact_messages_with_metadata(messages, _settings())

    assert changed is True
    assert compacted[0]["metadata"]["tokens_saved"] > 0
    assert metadata["tokens_saved"] == compacted[0]["metadata"]["tokens_saved"]
    assert metadata["cleared_attachment_ids"] == ["audio_1"]
    assert metadata["cleared_attachments"] == [{"message_id": "u_audio", "attachment_id": "audio_1"}]
