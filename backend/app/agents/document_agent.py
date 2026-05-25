from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan


class DocumentAgent(BaseAgent):
    name = "DocumentAgent"
    tool_prefix = "document."
    domain_summary = "Reads and analyzes PDF / DOCX / XLSX / PPTX / image documents; runs OCR via vision tools."
    prompt_file = "document_agent.md"

    def consult(self, plan: Plan) -> None:
        if any(step.agent_name == self.name or step.tool_name.startswith("document.") for step in plan.steps):
            self.bus.publish_text(
                plan.task_id,
                self.name,
                "Document extraction is read-only in MVP; semantic summaries can use AI only after privacy review.",
                message_type=MessageType.CRITIQUE,
            )
