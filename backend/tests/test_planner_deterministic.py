"""P3: 确定性规划模板单元测试(open-app 启动与按文件名搜索)。

这些计划在 ``PlannerAgent.create_plan`` 内零 LLM 生成,目的是绕过模型不确定
性。每个模板必须满足:意图命中时产出正确工具与参数;意图含糊或与删除/
卸载/清理/路径/网页冲突时让位给 LLM 规划。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.planner_agent import PlannerAgent
from app.core import db
from app.policy.risk import RiskLevel

TOOLS = [
    "file.search_by_name",
    "file.find_duplicates",
    "file.trash",
    "file.cleanup_plan",
    "app.launch_installed",
    "app.uninstall_app",
    "system.diagnostics",
]


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()
    yield


def _first_deterministic_plan(planner: PlannerAgent, goal: str):
    for build in (
        planner._deterministic_cleanup_plan,
        planner._deterministic_file_plan,
        planner._deterministic_uninstall_plan,
        planner._deterministic_system_check_plan,
        planner._deterministic_open_app_plan,
        planner._deterministic_search_plan,
    ):
        plan = build("task-1", goal, TOOLS)
        if plan:
            return plan
    return None


@pytest.mark.parametrize(
    ("goal", "expected_app"),
    [
        ("打开记事本", "notepad"),
        ("帮我打开计算器", "calculator"),
        ("open notepad", "notepad"),
        ("launch WhatsApp", "WhatsApp"),
        ("启动微信", "微信"),
    ],
)
def test_open_app_plan_extracts_app_name(goal: str, expected_app: str):
    plan = PlannerAgent()._deterministic_open_app_plan("task-1", goal, TOOLS)

    assert plan is not None
    step = plan.steps[0]
    assert step.tool_name == "app.launch_installed"
    assert step.agent_name == "AppAgent"
    assert step.args == {"app": expected_app}
    assert step.risk_level == RiskLevel.R1_OPEN_ONLY
    assert step.requires_approval is False
    assert plan.requires_user_approval is False
    assert plan.global_risk_level == RiskLevel.R1_OPEN_ONLY


@pytest.mark.parametrize(
    "goal",
    [
        "打开 D:\\docs\\报告.docx",  # Windows 路径 → 文件操作,交给 LLM
        "打开网站 example.com",  # 网页 → 浏览器领域
        "打开这个文件夹",  # 文件夹 → 文件领域
        "打开 https://example.com",
        "帮我卸载记事本",  # 卸载意图优先
        "删除并打开回收站",  # 删除意图让位
        "整理我的桌面",  # 无打开动词
    ],
)
def test_open_app_plan_declines_ambiguous_goals(goal: str):
    assert PlannerAgent()._deterministic_open_app_plan("task-1", goal, TOOLS) is None


def test_open_app_plan_requires_registered_tool():
    plan = PlannerAgent()._deterministic_open_app_plan("task-1", "打开记事本", ["file.search_by_name"])
    assert plan is None


@pytest.mark.parametrize(
    ("goal", "expected_query"),
    [
        ("帮我找一下文件：季度报告", "季度报告"),
        ("找一下 季度报告 文件", "季度报告"),
        ("搜索文件 发票2026", "发票2026"),
        ("find file quarterly report", "quarterly report"),
        ("search for files named budget", "budget"),
    ],
)
def test_search_plan_extracts_query(goal: str, expected_query: str):
    plan = PlannerAgent()._deterministic_search_plan("task-1", goal, TOOLS)

    assert plan is not None
    step = plan.steps[0]
    assert step.tool_name == "file.search_by_name"
    assert step.agent_name == "FileAgent"
    assert step.args == {"query": expected_query}
    assert step.risk_level == RiskLevel.R0_READ_ONLY
    assert plan.requires_user_approval is False
    assert plan.global_risk_level == RiskLevel.R0_READ_ONLY


@pytest.mark.parametrize(
    "goal",
    [
        "帮我查找重复文件",  # duplicates → file.find_duplicates 领域
        "find duplicate files",
        "找到并删除旧报告文件",  # 删除意图让位
        "清理文件",  # 清理意图让位
        "帮我找一下文件：",  # 空查询
        "搜索文件 C:\\Users\\me\\报告.docx",  # 显式路径交给 LLM
    ],
)
def test_search_plan_declines_ambiguous_goals(goal: str):
    assert PlannerAgent()._deterministic_search_plan("task-1", goal, TOOLS) is None


def test_search_plan_requires_registered_tool():
    plan = PlannerAgent()._deterministic_search_plan("task-1", "帮我找一下文件：季度报告", ["app.launch_installed"])
    assert plan is None


@pytest.mark.asyncio
async def test_create_plan_prefers_existing_templates_over_new_ones():
    """分发顺序回归:清理/删除/卸载/体检意图不会被新模板劫持。"""
    planner = PlannerAgent()

    plan = await planner.create_plan("task-1", "帮我卸载 NotARealApp12345", "efficiency", TOOLS)
    assert [step.tool_name for step in plan.steps] == ["app.uninstall_app"]

    plan = await planner.create_plan("task-2", "帮我检查这台电脑", "efficiency", TOOLS)
    assert [step.tool_name for step in plan.steps] == ["system.diagnostics"]


@pytest.mark.asyncio
async def test_create_plan_dispatches_new_templates_without_llm():
    planner = PlannerAgent()

    plan = await planner.create_plan("task-1", "打开记事本", "efficiency", TOOLS)
    assert [step.tool_name for step in plan.steps] == ["app.launch_installed"]
    assert plan.steps[0].args == {"app": "notepad"}

    plan = await planner.create_plan("task-2", "帮我找一下文件：季度报告", "efficiency", TOOLS)
    assert [step.tool_name for step in plan.steps] == ["file.search_by_name"]
    assert plan.steps[0].args == {"query": "季度报告"}
