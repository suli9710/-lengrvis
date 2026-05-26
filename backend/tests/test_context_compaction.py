from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_context import router
from app.config import AppSettings
from app.context_compaction import MANUAL_COMPACT_BOUNDARY, compact_session_context, compact_task_context, manual_compact_messages
from app.context_management import project_messages_for_llm
from app.core import db
from app.core.session_context import SessionContextStore
from app.core.schemas import MessageType
from app.orchestration.agent_bus import AgentBus


def _settings(**overrides) -> AppSettings:
    settings = AppSettings(
        model_context_window=2000,
        model_auto_compact_token_limit=1600,
        max_tokens=200,
        context_recent_message_limit=3,
        context_history_snip_enabled=False,
        context_micro_compact_enabled=False,
        context_auto_compact_enabled=False,
        context_session_summary_limit=1000,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _messages(count: int = 8) -> list[dict]:
    return [
        {"id": "system_1", "role": "system", "content": "Keep answers concise."},
        *[
            {"id": f"msg_{index}", "role": "user" if index % 2 else "assistant", "content": f"message {index} " + ("x" * 80)}
            for index in range(count)
        ],
    ]


def test_manual_compact_creates_boundary_summary_and_tail():
    result = manual_compact_messages(
        _messages(),
        _settings(context_recent_message_limit=2),
        custom_instructions="Preserve open decisions.",
    )

    assert result.boundary_message["metadata"]["context_boundary"] == MANUAL_COMPACT_BOUNDARY
    assert result.boundary_message["metadata"]["compaction_strategy"] == MANUAL_COMPACT_BOUNDARY
    assert result.boundary_message["metadata"]["compacted_at"]
    assert result.boundary_message["metadata"]["custom_instructions"] == "Preserve open decisions."
    assert result.messages[0]["role"] == "system"
    assert result.messages[1] == result.boundary_message
    assert [message["id"] for message in result.messages[-2:]] == ["msg_6", "msg_7"]
    assert result.pre_compact_tokens > result.post_compact_tokens
    assert "Preserve open decisions." in result.summary
    assert result.compact_metadata
    assert result.compact_metadata["original_messages"] == result.original_count
    assert result.compact_metadata["compacted_count"] == result.compacted_count
    assert result.compact_metadata["summary_chars"] == len(result.summary)


def test_manual_compact_keeps_tool_call_pair_when_tail_starts_on_tool_result():
    messages = _messages(count=5)
    messages.extend(
        [
            {
                "id": "call_owner",
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_manual",
                        "type": "function",
                        "function": {"name": "system.get_info", "arguments": "{}"},
                    }
                ],
            },
            {"id": "call_result", "role": "tool", "tool_call_id": "call_manual", "content": "manual tool output"},
            {"id": "after_tool", "role": "assistant", "content": "after manual tool"},
        ]
    )

    result = manual_compact_messages(messages, _settings(context_recent_message_limit=2))

    assert result.retained_tail_messages == 3
    assert result.boundary_message["metadata"]["retained_tail_message_ids"] == [
        "call_owner",
        "call_result",
        "after_tool",
    ]
    assert any(
        any(tool_call.get("id") == "call_manual" for tool_call in message.get("tool_calls") or [])
        for message in result.messages
    )
    assert any(message.get("tool_call_id") == "call_manual" for message in result.messages)


def test_manual_compact_updates_session_context_summary_and_token_stats():
    store = SessionContextStore(session_id="session_manual_compact_test")

    result = compact_session_context(
        _messages(),
        _settings(context_recent_message_limit=2),
        custom_instructions="Remember the implementation plan.",
        session_store=store,
    )

    session = result.session_context or {}
    assert "Remember the implementation plan." in session["conversation_summary"]
    assert session["last_summarized_message_id"] == "msg_7"
    assert session["token_stats"]["strategy"] == MANUAL_COMPACT_BOUNDARY
    assert session["token_stats"]["session_id"] == "session_manual_compact_test"
    assert session["token_stats"]["pre_compact_tokens"] == result.pre_compact_tokens
    assert session["token_stats"]["post_compact_tokens"] == result.post_compact_tokens
    assert session["token_stats"]["compact_metadata"]["compaction_strategy"] == MANUAL_COMPACT_BOUNDARY


def test_projection_uses_latest_compact_boundary_view():
    compacted = manual_compact_messages(_messages(), _settings(context_recent_message_limit=2)).messages
    transcript = [
        {"id": "older_system", "role": "system", "content": "Original policy."},
        {"id": "old_1", "role": "user", "content": "old history should be hidden"},
        *compacted,
        {"id": "new_1", "role": "user", "content": "new work"},
    ]

    projection = project_messages_for_llm(transcript, _settings(), source="test")
    contents = [message["content"] for message in projection.messages]

    assert "old history should be hidden" not in contents
    assert any(message.get("metadata", {}).get("context_boundary") == MANUAL_COMPACT_BOUNDARY for message in projection.messages)
    assert projection.messages[-1]["content"] == "new work"


def test_context_compact_route_returns_api_ready_payload():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/context/compact",
        json={
            "messages": _messages(),
            "custom_instructions": "Keep current TODOs.",
            "recent_message_limit": 2,
            "persist_session_context": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["boundary_message"]["metadata"]["context_boundary"] == MANUAL_COMPACT_BOUNDARY
    assert payload["retained_tail_messages"] == 2
    assert payload["pre_compact_tokens"] > payload["post_compact_tokens"]


def test_compact_task_context_persists_boundary_and_agent_bus_projection(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()
    task_id = "task_compact_persist"
    bus.publish_text(task_id, "User", "old history should be summarized", role="user", message_type=MessageType.OBSERVATION)
    for index in range(8):
        bus.publish_text(task_id, "PlannerAgent", f"message {index} " + ("x" * 80), message_type=MessageType.OBSERVATION)

    result = compact_task_context(
        task_id,
        _settings(context_recent_message_limit=2),
        custom_instructions="Keep current TODOs.",
        bus=bus,
        persist_session_context=False,
    )

    persisted = bus.get_messages(task_id)
    assert result.persisted_message_id
    assert any(message.id == result.persisted_message_id for message in persisted)
    projected = bus.get_llm_messages(task_id, _settings())
    contents = [message.get("content") for message in projected]
    assert "old history should be summarized" not in contents
    assert any(message.get("metadata", {}).get("context_boundary") == MANUAL_COMPACT_BOUNDARY for message in projected)
    assert any("Keep current TODOs." in str(message.get("content") or "") for message in projected)
    assert any("message 7" in str(content or "") for content in contents)


def test_context_compact_route_can_persist_task_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()
    task_id = "task_compact_route"
    for index in range(6):
        bus.publish_text(task_id, "PlannerAgent", f"route message {index} " + ("x" * 60), message_type=MessageType.OBSERVATION)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    response = client.post(
        "/api/context/compact",
        json={
            "task_id": task_id,
            "custom_instructions": "Preserve route TODOs.",
            "recent_message_limit": 2,
            "persist_session_context": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["persisted_message_id"]
    assert any(
        message.metadata.get("context_boundary") == MANUAL_COMPACT_BOUNDARY
        for message in bus.get_messages(task_id)
    )
