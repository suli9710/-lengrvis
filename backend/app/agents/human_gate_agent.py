from __future__ import annotations

from app.agents.base import BaseAgent


class HumanGateAgent(BaseAgent):
    name = "HumanGateAgent"
    domain_summary = "Coordinates human-in-the-loop approval gates and explains why a task needs confirmation."
    prompt_file = "human_gate_agent.md"
