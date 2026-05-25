from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.schemas import AgentAction, MessageType, PlanStep, ToolResult
from app.llm.prompts import load_prompt, render_prompt
from app.orchestration.agent_bus import AgentBus


@dataclass(slots=True)
class AgentContext:
    task_id: str
    mode: str
    allowed_directories: list[str]


class BaseAgent:
    name = "BaseAgent"
    tool_prefix = ""  # subclasses override; used by allowed_tools()
    domain_summary = ""  # one-line summary used in act() prompts
    prompt_file = ""  # optional markdown prompt in app/llm/prompts/

    def __init__(self, bus: AgentBus | None = None) -> None:
        self.bus = bus or AgentBus()

    def system_prompt(self) -> str:
        """Subclasses override to inject role-specific guidance into LLM prompts."""
        if self.prompt_file:
            prompt = load_prompt(self.prompt_file)
            if prompt:
                return prompt
        return render_prompt("base_agent.md", {"agent_name": self.name})

    def allowed_tools(self, registry=None) -> list[str]:
        """Return tool names this agent is permitted to invoke."""
        if registry is None:
            try:
                from app.tools.registry import registry as default_registry

                registry = default_registry
            except Exception:
                return []
        result: list[str] = []
        for tool in registry.list():
            if getattr(tool, "agent_owner", "") == self.name:
                result.append(tool.name)
            elif self.tool_prefix and tool.name.startswith(self.tool_prefix):
                result.append(tool.name)
        return result

    async def act(
        self,
        step: PlanStep,
        context: AgentContext,
        observation: ToolResult | None = None,
        *,
        provider=None,
    ) -> AgentAction:
        """Self-reason about the next move. Returns an AgentAction.

        Subclasses can override; the default implementation calls the configured
        structured LLM provider with the agent's system_prompt + the step + the
        latest observation.
        """
        from app.llm.registry import get_provider

        prov = provider or get_provider(task="subagent")
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": _act_user_prompt(step, observation, self.allowed_tools(), context)},
        ]
        schema = {
            "type": "object",
            "required": ["kind"],
            "properties": {
                "kind": {"type": "string", "enum": ["propose_tool", "request_revision", "done"]},
                "tool_name": {"type": "string"},
                "args": {"type": "object"},
                "rationale": {"type": "string"},
                "follow_up_question": {"type": "string"},
            },
        }
        try:
            payload = await prov.structured_chat(messages, schema)
            return AgentAction(**{k: payload.get(k, "") for k in ("kind", "tool_name", "rationale", "follow_up_question")},
                               args=payload.get("args") or {})
        except Exception as exc:  # noqa: BLE001
            return AgentAction(
                kind="request_revision",
                rationale=f"{self.name} failed to plan an action: {exc}",
            )

    async def reflect(
        self,
        step: PlanStep,
        result: ToolResult,
        *,
        provider=None,
    ) -> str:
        """Summarize the tool result back into the message bus as an OBSERVATION."""
        summary = _format_reflection(step, result, self.name, self.domain_summary)
        try:
            self.bus.publish_text(
                step.task_id or "",
                self.name,
                summary,
                message_type=MessageType.OBSERVATION,
                step_id=step.id,
                structured_payload={"reflection": True, "step_id": step.id, "ok": result.ok},
            )
        except Exception:
            # Bus failures should not break orchestration.
            pass
        return summary


def _act_user_prompt(
    step: PlanStep,
    observation: ToolResult | None,
    allowed: list[str],
    context: AgentContext,
) -> str:
    observation_block = ""
    if observation is not None:
        ok_text = "succeeded" if observation.ok else "failed"
        observation_block = f"Last observation {ok_text}: {observation.observation or observation.error}"
    return render_prompt(
        "agent_act_user.md",
        {
            "task_mode": context.mode,
            "authorized_directories": context.allowed_directories,
            "plan_step_description": step.description,
            "proposed_tool": step.tool_name,
            "proposed_args": step.args,
            "risk_level": step.risk_level,
            "allowed_tools": ", ".join(allowed) or "(none)",
            "observation_block": observation_block,
        },
    )


def _format_reflection(step: PlanStep, result: ToolResult, agent_name: str, domain_summary: str) -> str:
    if result.ok:
        body = result.observation or "completed"
        return f"[{agent_name}] {step.tool_name} {body}."
    return f"[{agent_name}] {step.tool_name} failed: {result.error or 'unknown error'}."

