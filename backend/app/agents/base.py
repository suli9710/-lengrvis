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
    registry: Any | None = None


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
        structured LLM provider only when a deterministic policy cannot safely
        accept or reject the planned tool call.
        """
        deterministic = self._deterministic_action(step, context, observation)
        if deterministic is not None:
            return deterministic

        from app.llm.registry import get_provider

        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": _act_user_prompt(step, observation, self.allowed_tools(context.registry), context)},
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
            prov = provider or get_provider(task="subagent")
            payload = await prov.structured_chat(messages, schema)
            return AgentAction(**{k: payload.get(k, "") for k in ("kind", "tool_name", "rationale", "follow_up_question")},
                               args=payload.get("args") or {})
        except Exception as exc:  # noqa: BLE001
            return AgentAction(
                kind="request_revision",
                rationale=f"{self.name} failed to plan an action: {exc}",
            )

    def _deterministic_action(
        self,
        step: PlanStep,
        context: AgentContext,
        observation: ToolResult | None = None,
    ) -> AgentAction | None:
        """Fast path for clear worker execution steps.

        The worker accepts a planner-proposed tool call without an LLM hop when
        the tool is registered, owned by this agent, and satisfies the declared
        JSON-schema required fields. Failed observations still go to the LLM so
        recovery can reason about alternatives.
        """
        if observation is not None and not observation.ok:
            return None
        if not step.tool_name:
            return AgentAction(
                kind="request_revision",
                rationale=f"{self.name} cannot execute a step without a tool name.",
                follow_up_question="Which tool should this worker execute?",
            )

        registry = context.registry or _default_registry()
        if registry is None:
            return None
        try:
            tool = registry.get(step.tool_name)
        except KeyError:
            return AgentAction(
                kind="request_revision",
                rationale=f"{self.name} cannot find registered tool {step.tool_name}.",
                follow_up_question=f"Select an available {self.name} tool or use tool.search to discover one.",
            )

        owner = getattr(tool, "agent_owner", "")
        if owner != self.name:
            return AgentAction(
                kind="request_revision",
                rationale=f"{self.name} cannot execute {step.tool_name}; it is owned by {owner or 'another agent'}.",
                follow_up_question="Route this step to the owning worker or choose one of this worker's allowed tools.",
            )
        if getattr(tool, "defer_loading", False):
            return None
        schema = getattr(tool, "input_schema", {}) or {}

        missing_required = _missing_required_args(schema, step.args or {})
        if missing_required:
            return AgentAction(
                kind="request_revision",
                rationale=f"{self.name} needs required argument(s): {', '.join(missing_required)}.",
                follow_up_question=f"Provide {', '.join(missing_required)} for {step.tool_name}.",
            )
        if not _can_accept_planned_tool(tool, step):
            return None

        return AgentAction(
            kind="propose_tool",
            tool_name=step.tool_name,
            args=dict(step.args or {}),
            rationale=f"{self.name} accepted the planned tool call via deterministic fast path.",
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


def _default_registry():
    try:
        from app.tools.registry import registry
    except Exception:
        return None
    return registry


def _missing_required_args(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    if schema.get("type") not in {"object", None}:
        return []
    required = [str(item) for item in schema.get("required") or [] if str(item).strip()]
    missing: list[str] = []
    for key in required:
        value = args.get(key)
        if value is None or value == "":
            missing.append(key)
    return missing


def _can_accept_planned_tool(tool: Any, step: PlanStep) -> bool:
    if getattr(tool, "fast_path_eligible", False) and _has_explicit_object_schema(getattr(tool, "input_schema", {}) or {}):
        return True
    risk_value = getattr(getattr(tool, "risk_level", None), "value", str(getattr(tool, "risk_level", "") or ""))
    return (
        risk_value.startswith(("R2", "R3"))
        and bool(getattr(tool, "supports_dry_run", False))
        and (step.requires_approval or bool((step.args or {}).get("dry_run")))
    )


def _has_explicit_object_schema(schema: dict[str, Any]) -> bool:
    """Require declared object inputs before bypassing worker LLM reasoning."""
    if schema.get("type") != "object":
        return False
    properties = schema.get("properties")
    required = schema.get("required") or []
    return isinstance(properties, dict) and (
        bool(properties) or bool(required) or "additionalProperties" in schema
    )
