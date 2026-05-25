from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan


class FileAgent(BaseAgent):
    name = "FileAgent"
    tool_prefix = "file."
    domain_summary = "Owns file search, indexing, copy / move / rename / trash, and authorized-path constraints."
    prompt_file = "file_agent.md"

    def consult(self, plan: Plan) -> None:
        if any(step.agent_name == self.name or step.tool_name.startswith("file.") for step in plan.steps):
            self.bus.publish_text(
                plan.task_id,
                self.name,
                "File paths must stay inside authorized directories; modifying steps need dry-run previews.",
                message_type=MessageType.CRITIQUE,
            )
