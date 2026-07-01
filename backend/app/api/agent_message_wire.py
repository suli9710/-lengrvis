from __future__ import annotations

from typing import Any

from app.context.agent_message_projection import llm_safe_agent_message
from app.core.schemas import AgentMessage


def wire_safe_agent_message(message: AgentMessage) -> dict[str, Any]:
    return llm_safe_agent_message(message, include_legacy=True, redact_user_content=True)
