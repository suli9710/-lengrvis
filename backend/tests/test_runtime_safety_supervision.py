from __future__ import annotations

import pytest

import app.agents.planner_agent as planner_module
from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import AgentMessage, MessageType, Plan, PlanStep, StepStatus
from app.orchestration.task_phase import TaskPhase
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


class DoneComputerAgent:
    name = "ComputerAgent"

    def consult(self, plan):  # noqa: ANN001, ANN201, ARG002
        if any(step.agent_name == self.name for step in plan.steps):
            self.bus.publish_text(
                plan.task_id,
                self.name,
                f"{self.name} deterministic consultation.",
                message_type=MessageType.CRITIQUE,
            )
        return None

    def __init__(self, bus=None):  # noqa: ANN001
        self.bus = bus

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        return None

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "reflected"


class DoneAppAgent(DoneComputerAgent):
    name = "AppAgent"


def test_runtime_supervision_allows_internal_payload_fields():
    message = AgentMessage(
        task_id="task_supervision",
        from_agent="OrchestratorAgent",
        message_type=MessageType.PROPOSAL,
        content="Calling tool system.get_info.",
        structured_payload={"tool_name": "system.get_info"},
    )

    review = PolicyEngine().review_agent_message(message, "tool_call_proposed")

    assert review.verdict == "allow"


def test_runtime_supervision_blocks_sensitive_agent_message():
    message = AgentMessage(
        task_id="task_supervision",
        from_agent="BrowserAgent",
        message_type=MessageType.PROPOSAL,
        content="Read browser cookie and token values.",
    )

    review = PolicyEngine().review_agent_message(message, "browser_consultation")

    assert review.verdict == "deny"
    assert review.risk_level == "R4_FORBIDDEN_OR_HANDOFF"


def test_safety_review_agent_accepts_tool_definition_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    orchestrator = OrchestratorAgent()
    tool = ToolDefinition(
        name="system.get_info",
        description="system get info",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="ComputerAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=lambda args, context: {"ok": True},
        effects=["read"],
        resource_kinds=["system"],
        fast_path_eligible=True,
        trust_tier="builtin",
    )

    review = orchestrator.safety.review_tool_call(
        "task_fast_agent",
        "step_fast_agent",
        tool.name,
        {},
        tool.risk_level,
        tool_definition=tool,
    )

    assert review.verdict == "allow"
    assert "fast path" in " ".join(review.reasons).lower()


@pytest.mark.anyio
async def test_orchestrator_records_full_safety_supervision(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()
    monkeypatch.setattr(planner_module, "get_provider", lambda: _system_info_plan_provider())

    task = await OrchestratorAgent().handle_user_goal("check system information", "privacy")
    reviews = db.fetch_many("safety_reviews", "task_id = ?", (task.id,), limit=100)
    target_types = {review["target_type"] for review in reviews}

    assert task.status == "completed"
    assert {
        "agent_message:user_goal",
        "agent_message:planner_output",
        "agent_message:ComputerAgent_consultation",
        "tool_call",
        "agent_message:tool_call_proposed",
        "tool_result",
        "agent_message:tool_observation",
        "final",
    }.issubset(target_types)


@pytest.mark.anyio
async def test_orchestrator_app_agent_is_supervised_for_app_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()

    async def app_plan(*args, **kwargs):
        from app.core.schemas import Plan, PlanStep
        from app.policy.risk import RiskLevel

        task_id = args[0]
        return Plan(
            task_id=task_id,
            goal="open notepad",
            steps=[
                PlanStep(
                    task_id=task_id,
                    agent_name="AppAgent",
                    tool_name="app.launch_installed",
                    description="Open Notepad through the allowlisted app launcher.",
                    args={"app": "notepad", "dry_run": True},
                    risk_level=RiskLevel.R1_OPEN_ONLY,
                )
            ],
        )

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["AppAgent"] = DoneAppAgent(orchestrator.bus)
    monkeypatch.setattr(orchestrator.planner, "create_plan", app_plan)

    task = await orchestrator.handle_user_goal("open notepad", "privacy")
    target_types = {
        review["target_type"]
        for review in db.fetch_many("safety_reviews", "task_id = ?", (task.id,), limit=100)
    }

    assert task.status == "completed"
    assert "agent_message:AppAgent_consultation" in target_types


@pytest.mark.anyio
async def test_tool_call_denial_keeps_task_denied(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["ComputerAgent"] = DoneComputerAgent(orchestrator.bus)
    orchestrator.registry.register(
        ToolDefinition(
                name="test.forbidden_runtime",
                description="forbidden runtime tool",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                output_schema={},
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                agent_owner="ComputerAgent",
                supports_dry_run=False,
                requires_authorized_path=False,
                execute=lambda args, context: {"ok": True},  # noqa: ARG005
                fast_path_eligible=True,
            )
        )

    async def forbidden_plan(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        task_id = args[0]
        return Plan(
            task_id=task_id,
            goal="forbidden tool",
            steps=[
                PlanStep(
                    task_id=task_id,
                    agent_name="ComputerAgent",
                    tool_name="test.forbidden_runtime",
                    description="Call a tool whose registry definition is forbidden.",
                    args={},
                    risk_level=RiskLevel.R0_READ_ONLY,
                )
            ],
        )

    monkeypatch.setattr(orchestrator.planner, "create_plan", forbidden_plan)

    task = await orchestrator.handle_user_goal("run forbidden tool", "privacy")
    plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)[0])

    assert task.status == TaskPhase.CANCELLED
    assert plan.steps[0].status == StepStatus.DENIED
    assert "denied" in task.final_summary.lower()


def _system_info_plan_provider():
    class _Provider:
        async def structured_chat(self, messages, output_schema):
            return {
                "goal": "check system information",
                "steps": [
                    {
                        "agent_name": "ComputerAgent",
                        "tool_name": "system.get_info",
                        "description": "Read system information.",
                        "args": {},
                        "risk_level": "R0_READ_ONLY",
                    }
                ],
            }

    return _Provider()
