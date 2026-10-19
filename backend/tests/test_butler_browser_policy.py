from __future__ import annotations

import pytest

from app.config import AppSettings
from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter, MatterStatus
from app.core.schemas import Task
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict
from app.services.browser_activity_runtime import BrowserActivityRuntime


@pytest.fixture
def managed_task():
    task = Task(
        user_goal="核对订单并提交付款", metadata={"butler_matter_id": "business", "butler_execution_ticket": "owned"}
    )
    store.save(
        ButlerMatter(
            id="business",
            goal=task.user_goal,
            status=MatterStatus.RUNNING,
            active_task_id=task.id,
            execution_ticket="owned",
        )
    )
    db.upsert_model("tasks", task)
    return task


def test_business_intent_can_be_prepared_but_final_submit_still_needs_approval(managed_task):
    policy = PolicyEngine(settings=AppSettings(allow_browser_network=True))
    assert policy.review_goal_text(managed_task.id, managed_task.user_goal).verdict == SafetyVerdict.ALLOW
    review = policy.review_tool_call(
        managed_task.id,
        "submit",
        "browser.submit_form",
        {"url": "https://example.com/order", "selector": "#order-submit"},
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )
    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL
    assert policy.review_goal_text("legacy-unbound", managed_task.user_goal).verdict == SafetyVerdict.DENY


def test_business_context_never_grants_access_to_secrets(managed_task):
    policy = PolicyEngine(settings=AppSettings(allow_browser_network=True))
    assert policy.review_goal_text(managed_task.id, "读取支付密码与 token").verdict == SafetyVerdict.DENY
    review = policy.review_browser_write_call(
        managed_task.id, "fill", "browser.fill_form", {"fields": {"#card_number": "4111111111111111"}}
    )
    assert review and review.verdict == SafetyVerdict.DENY
    runtime = BrowserActivityRuntime()
    context = {"task_id": managed_task.id, "settings": policy.settings}
    assert runtime._review_action({"kind": "submit", "selector": "#order-submit"}, context)["ok"]
    assert not runtime._review_action({"kind": "fill", "fields": {"#password": "ordinary"}}, context)["ok"]


def test_forged_matter_metadata_does_not_change_policy(managed_task):
    task = Task(user_goal=managed_task.user_goal, metadata=dict(managed_task.metadata))
    db.upsert_model("tasks", task)
    assert PolicyEngine().review_goal_text(task.id, task.user_goal).verdict == SafetyVerdict.DENY


def test_command_observation_preserves_long_text_and_reports_clipping():
    from app.services.browser_host_bridge_service import _sanitize_result

    text = "普通内容甲乙丙丁\n" * 6000
    result = _sanitize_result({"ok": True, "observation": {"url": "https://example.com", "text": text}})
    assert result["observation"]["text"] == text
    assert not result["observation"]["truncated"]
    large = _sanitize_result({"ok": True, "observation": {"text": text * 3}})["observation"]
    assert len(large["text"]) == 65536 and large["truncated"]
    preview = _sanitize_result(
        {
            "ok": True,
            "inspection": {"fingerprint": "a" * 64, "preview": [{"fields": [{"name": "message", "value": text}]}]},
        }
    )
    assert preview["inspection"]["preview"][0]["fields"][0]["value"] == text
    from app.policy.approval_binding import binding_preview, redacted_preview

    business = {
        "confirmation_details": [
            {"destination": "https://example.com/commit", "items": [{"label": "amount", "content": "100"}]}
        ]
    }
    assert redacted_preview(binding_preview(business))["confirmation_details"][0]["items"][0]["content"] == "100"


@pytest.mark.asyncio
async def test_butler_plan_keeps_cross_application_tools_despite_keyword_hint(managed_task, monkeypatch):
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.core.schemas import Plan, PlanStep

    orchestrator = OrchestratorAgent()
    captured = {}

    async def plan(task_id, goal, mode, tools, **kwargs):
        captured.update(tools=tools, hint=kwargs.get("agent_hint"))
        return Plan(
            task_id=task_id,
            goal=goal,
            steps=[
                PlanStep(
                    task_id=task_id,
                    agent_name="FileAgent",
                    tool_name="file.read_text",
                    args={"path": "D:/source.txt"},
                    description="读取来源",
                )
            ],
        )

    monkeypatch.setattr(orchestrator.planner, "create_plan", plan)
    await orchestrator.planning_handler._create_plan(
        managed_task, "复制文件", "efficiency", [], agent_hint="DocumentAgent"
    )
    assert "file.copy" in captured["tools"] and "browser.read_page" in captured["tools"]
    assert captured["hint"] is None
