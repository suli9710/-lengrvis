"""Sprint 3: shared delegation keyword routing and English verb coverage."""

from __future__ import annotations

import pytest

from app.agents.delegation_metadata import infer_supervisor_agent_hint
from app.agents.delegation_rules import contains_term
from app.agents.supervisor_agent import SupervisorAgent


def test_contains_term_english_word_boundary():
    assert contains_term("please delete the file", "delete")
    assert not contains_term("movie night", "move")
    assert contains_term("please move the file", "move")


def test_infer_supervisor_agent_hint_english_file_verbs():
    assert infer_supervisor_agent_hint("delete old logs in Downloads") == "FileAgent"
    assert infer_supervisor_agent_hint("copy report.pdf to backup folder") == "FileAgent"
    assert infer_supervisor_agent_hint("rename config.txt") == "FileAgent"
    assert infer_supervisor_agent_hint("remove duplicate files") == "FileAgent"


def test_infer_supervisor_agent_hint_english_app_verbs():
    assert infer_supervisor_agent_hint("uninstall notepad") == "AppAgent"


def test_supervisor_heuristic_routes_english_delete_with_path():
    decision = SupervisorAgent().quick_decision("delete C:\\Users\\me\\Downloads\\old.zip")
    assert decision.delegate is True
    assert decision.agent_hint == "FileAgent"


def test_supervisor_heuristic_does_not_delegate_movie():
    decision = SupervisorAgent().quick_decision("let's watch a movie tonight")
    assert decision.delegate is False


@pytest.mark.asyncio
async def test_supervisor_handles_expired_memory_reconfirmation_without_provider(monkeypatch):
    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("expired memory safety reply must not call a provider")

    monkeypatch.setattr("app.agents.supervisor_agent.get_provider", provider_must_not_run)

    decision = await SupervisorAgent().decide(
        "检查默认报告收件人偏好是否仍有效；如果旧偏好已经超过 TTL，请不要使用旧值并明确说明需要重新确认。",
        "efficiency",
    )

    assert decision.delegate is False
    assert decision.agent_hint == ""
    assert "重新确认" in decision.reply


@pytest.mark.asyncio
async def test_response_only_benchmark_contracts_do_not_depend_on_provider(monkeypatch):
    from scripts.real_llm_benchmark_catalog import load_real_llm_benchmark

    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("response-only safety contracts must not call a provider")

    monkeypatch.setattr("app.agents.supervisor_agent.get_provider", provider_must_not_run)
    _catalog, cases = load_real_llm_benchmark()
    selected_scenarios = {
        "memory-expired-preference",
        "mobile-status-query",
        "mobile-short-follow-up",
    }
    selected = [case for case in cases if case["benchmark"]["scenario_id"] in selected_scenarios]
    assert len(selected) == 9

    for case in selected:
        decision = await SupervisorAgent().decide(case["message"], "efficiency")
        assert decision.delegate is False, case["id"]
        assert decision.agent_hint == "", case["id"]
        if case["benchmark"]["scenario_id"] == "memory-expired-preference":
            assert "重新确认" in decision.reply, case["id"]
        else:
            assert "任务 ID" in decision.reply, case["id"]
            assert "不会创建或执行新任务" in decision.reply, case["id"]


def test_expired_memory_shortcut_does_not_swallow_explicit_revoke():
    decision = SupervisorAgent().quick_decision("撤销已经过期的偏好 mem_123，并重新确认新值。")

    assert decision.delegate is True
    assert decision.agent_hint == "MemoryAgent"


def test_infer_supervisor_agent_hint_chinese_file_and_app():
    assert infer_supervisor_agent_hint("删除下载目录里的旧日志") == "FileAgent"
    assert infer_supervisor_agent_hint("打开下载文件夹") == "FileAgent"
    assert infer_supervisor_agent_hint("卸载微信") == "AppAgent"
    assert infer_supervisor_agent_hint("删除这个文件") == "FileAgent"


def test_engine_route_chinese_os_and_developer_goals():
    from app.orchestration.engine_router import route_engine

    uninstall = route_engine("卸载微信")
    assert uninstall.selected_engine == "os"
    assert uninstall.rule == "os_goal"

    folder = route_engine("打开下载文件夹")
    assert folder.selected_engine == "os"
    assert folder.rule == "os_goal"

    fix_bug = route_engine("帮我修复登录功能的 bug")
    assert fix_bug.selected_engine == "os"
    assert fix_bug.rule == "developer_write_os"

    inspect = route_engine("分析一下这个仓库的代码结构")
    assert inspect.selected_engine == "developer"
    assert inspect.rule == "developer_read_only"

    diagnostics = route_engine("帮我检查这台电脑")
    assert diagnostics.selected_engine == "os"
    assert diagnostics.rule == "system_diagnostics"

    # Logic review: single-char write triggers must not false-positive.
    assert route_engine("改革开放的历史").rule != "developer_write_os"
