from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan


class DocumentAgent(BaseAgent):
    name = "DocumentAgent"
    tool_prefix = "document."
    domain_summary = "Reads documents and creates sourced Excel/Word outputs in authorized butler matters."
    prompt_file = "document_agent.md"

    def consult(self, plan: Plan) -> None:
        if any(step.agent_name == self.name or step.tool_name.startswith("document.") for step in plan.steps):
            self.bus.publish_text(
                plan.task_id,
                self.name,
                "Read source contents before analysis. New Office outputs require scoped previews, "
                "saved-file readback and verification; preserve existing documents and source citations.",
                message_type=MessageType.CRITIQUE,
            )
