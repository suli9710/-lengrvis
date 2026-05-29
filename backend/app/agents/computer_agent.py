from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan


class ComputerAgent(BaseAgent):
    name = "ComputerAgent"
    tool_prefix = "system."
    domain_summary = "Inspects host CPU / RAM / disk / processes / startup items and proposes safe cleanup, remote desktop, or GUI automation actions."
    prompt_file = "computer_agent.md"

    def allowed_tools(self, registry=None) -> list[str]:
        allowed = super().allowed_tools(registry)
        if registry is None:
            return allowed
        for tool in registry.list():
            if tool.name.startswith(("remote.", "ui_automation.")) and tool.name not in allowed:
                allowed.append(tool.name)
        return allowed

    def consult(self, plan: Plan) -> None:
        if any(step.agent_name == self.name or step.tool_name.startswith(("system.", "remote.", "ui_automation.")) for step in plan.steps):
            self.bus.publish_text(
                plan.task_id,
                self.name,
                "System inspection is read-only unless a Windows settings, remote input, or GUI automation operation is explicitly approved.",
                message_type=MessageType.CRITIQUE,
            )
