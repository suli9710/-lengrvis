from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan


class SearchAgent(BaseAgent):
    name = "SearchAgent"
    tool_prefix = "search."
    domain_summary = "Queries web search and external MCP servers; returns sourced answers with URL, title, retrieved_at."
    prompt_file = "search_agent.md"

    def consult(self, plan: Plan) -> None:
        if any(step.agent_name == self.name or step.tool_name.startswith("search.") for step in plan.steps):
            self.bus.publish_text(
                plan.task_id,
                self.name,
                "External search results must preserve source URL, title, summary, and retrieval time.",
                message_type=MessageType.CRITIQUE,
            )
