from __future__ import annotations

import json

from app.api.task_public_views import openai_agent_messages, public_agent_messages
from app.core import db
from app.orchestration.agent_bus import AgentBus, flush_agent_message_writes


def test_openai_agent_messages_use_llm_safe_projection(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()
    task_id = "task_openai_view_safe"
    local_path = r"C:\Users\Suli\Desktop\mavris\.env"
    secret = "sk-openai-view-secret-value"

    message = bus.publish_text(
        task_id,
        "PlannerAgent",
        f"Tool failed while reading {local_path} token={secret}",
        structured_payload={"path": local_path, "api_key": secret},
        metadata={"error": f"raw error {local_path} token={secret}"},
        tool_calls=[
            {
                "id": "call_openai_view",
                "type": "function",
                "function": {"name": "file.read_text", "arguments": {"path": local_path, "api_key": secret}},
            }
        ],
    )

    assert flush_agent_message_writes(timeout_seconds=10)
    persisted = next(item for item in bus.get_messages(task_id, limit=10) if item.id == message.id)
    assert local_path in persisted.content
    assert secret in persisted.content
    assert persisted.structured_payload["path"] == local_path
    assert persisted.structured_payload["api_key"] == secret

    [payload] = openai_agent_messages(task_id)
    dumped = str(payload)
    assert local_path not in dumped
    assert secret not in dumped
    assert "[REDACTED_LOCAL_PATH]" in dumped
    assert payload["metadata"]["structured_payload"]["api_key"] == "***"
    arguments = json.loads(payload["tool_calls"][0]["function"]["arguments"])
    assert arguments["path"] == "[REDACTED_LOCAL_PATH]"
    assert arguments["api_key"] == "***"


def test_public_agent_messages_use_shared_projection_before_public_redaction(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()
    task_id = "task_public_message_safe"
    local_path = r"C:\Users\Suli\Desktop\mavris\.env"
    secret = "sk-public-message-secret-value"

    message = bus.publish_text(
        task_id,
        "PlannerAgent",
        f"Reading {local_path} token={secret}",
        structured_payload={"path": local_path, "api_key": secret},
        metadata={"error": f"raw error {local_path} token={secret}"},
        tool_calls=[
            {
                "id": "call_public_view",
                "type": "function",
                "function": {"name": "file.read_text", "arguments": {"path": local_path, "api_key": secret}},
            }
        ],
    )

    assert flush_agent_message_writes(timeout_seconds=10)
    persisted = next(item for item in bus.get_messages(task_id, limit=10) if item.id == message.id)
    assert local_path in persisted.content
    assert secret in persisted.content

    [payload] = public_agent_messages(task_id)
    dumped = str(payload)
    assert local_path not in dumped
    assert secret not in dumped
    assert payload["content"] == "Agent message recorded."
    assert payload["metadata"]["structured_payload"] == {"redacted": True, "field_count": 2}
    assert payload["structured_payload"]["path"] == "[REDACTED_LOCAL_PATH]"
    assert payload["structured_payload"]["api_key"] == "***"
    assert payload["tool_calls"][0]["id"] == "call_public_view"
    assert payload["tool_calls"][0]["function"] == {"redacted": True, "field_count": 2}
    assert payload["redacted"] is True
