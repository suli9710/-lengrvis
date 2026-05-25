from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan


class BrowserAgent(BaseAgent):
    name = "BrowserAgent"
    tool_prefix = "browser."
    domain_summary = "Reads web pages, takes screenshots, performs R2/R3 clicks / fills only in efficiency mode with explicit approval."
    prompt_file = "browser_agent.md"

    def consult(self, plan: Plan) -> None:
        if any(step.agent_name == self.name or step.tool_name.startswith("browser.") for step in plan.steps):
            self.bus.publish_text(
                plan.task_id,
                self.name,
                "Browser operations start read-only; login, payment, submission, and messaging are handoff-only.",
                message_type=MessageType.CRITIQUE,
            )
