from __future__ import annotations

from app.core.schemas import AgentMessage, MessageType, OpenAIMessageRole
from app.orchestration.agent_bus import AgentBus


def test_agent_message_exports_openai_compatible_shape():
    message = AgentMessage(
        task_id="task_openai",
        from_agent="PlannerAgent",
        message_type=MessageType.PROPOSAL,
        content="Plan ready.",
        structured_payload={"steps": []},
    )

    payload = message.to_openai_dict()

    assert payload["role"] == "assistant"
    assert payload["name"] == "PlannerAgent"
    assert payload["content"] == "Plan ready."
    assert payload["metadata"]["from_agent"] == "PlannerAgent"
    assert payload["metadata"]["message_type"] == "proposal"
    assert payload["metadata"]["structured_payload"] == {"steps": []}
    assert payload["from_agent"] == "PlannerAgent"


def test_legacy_agent_message_is_normalized_to_openai_fields():
    message = AgentMessage.model_validate(
        {
            "task_id": "task_openai",
            "from_agent": "SafetyReviewAgent",
            "message_type": "review",
            "content": "Allowed.",
        }
    )

    assert message.role == OpenAIMessageRole.ASSISTANT
    assert message.name == "SafetyReviewAgent"
    assert message.metadata["message_type"] == "review"


def test_agent_bus_can_publish_tool_call_message(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))

    message = AgentBus().publish_text(
        "task_openai",
        "OrchestratorAgent",
        "Calling tool system.get_info.",
        message_type=MessageType.PROPOSAL,
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "system.get_info", "arguments": {}},
            }
        ],
    )

    payload = message.to_openai_dict()

    assert payload["role"] == "assistant"
    assert payload["tool_calls"][0]["function"]["name"] == "system.get_info"

