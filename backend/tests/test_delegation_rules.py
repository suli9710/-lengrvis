"""Sprint 3: shared delegation keyword routing and English verb coverage."""

from __future__ import annotations

from app.agents.delegation_metadata import infer_supervisor_agent_hint
from app.agents.delegation_rules import contains_any, contains_term
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
