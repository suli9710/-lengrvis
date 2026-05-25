from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan


class AppAgent(BaseAgent):
    name = "AppAgent"
    tool_prefix = "app."
    domain_summary = "Lists installed apps, launches allow-listed binaries, opens files / folders, runs MSI uninstall flows, and performs allow-listed Excel COM operations."
    prompt_file = "app_agent.md"

    def consult(self, plan: Plan) -> None:
        if any(step.agent_name == self.name or step.tool_name.startswith("app.") for step in plan.steps):
            self.bus.publish_text(
                plan.task_id,
                self.name,
                "Application operations are limited to indexed apps and authorized file/folder open actions; unknown executables require approval or are blocked.",
                message_type=MessageType.CRITIQUE,
            )
