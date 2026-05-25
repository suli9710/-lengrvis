from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.memory_agent import MemoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.tools.registry import register_all_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    db.init_db()
    yield


def test_phase2_tools_are_registered():
    registry = register_all_tools(load_skills=False)

    assert registry.get("ui_automation.find_element").risk_level == "R0_READ_ONLY"
    assert registry.get("ui_automation.click").supports_dry_run is True
    assert registry.get("workflow.run").supports_dry_run is True


def test_workflow_tool_previews_cross_app_dag():
    registry = register_all_tools(load_skills=False)
    result = registry.get("workflow.run").execute(
        {
            "dry_run": True,
            "workflow": {
                "id": "wf_mail_to_wps",
                "steps": [
                    {"id": "open", "target_app": "wps.office", "action": "open_document"},
                    {
                        "id": "paste",
                        "target_app": "wps.office",
                        "action": "paste_clipboard",
                        "depends_on": ["open"],
                        "data_transfer": {"clipboard_text": "attachment"},
                    },
                ],
            },
        },
        {},
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["workflow_id"] == "wf_mail_to_wps"
    assert result["steps"][1]["data_transfer"]["clipboard_text"] == "attachment"


def test_app_skill_metadata_is_attached_to_registered_tools(test_data_dir: Path):
    registry = register_all_tools(skill_directories=[str(test_data_dir / "skills")])

    wechat = registry.get("skill.wechat_desktop.send_message")
    wps = registry.get("skill.wps_office.open_edit_document")

    assert wechat.app_target["app_id"] == "wechat.desktop"
    assert wechat.workflow["action"] == "send_message"
    assert wps.app_target["interface"] == "com"


def test_orchestrator_recall_memory_includes_lessons():
    memory = MemoryAgent()
    asyncio.run(
        memory.remember_lesson(
            {
                "goal_pattern": "open attachment in WPS",
                "tool": "workflow.run",
                "args_pattern": {"target_app": "wps.office"},
                "outcome": "succeeded",
                "reason": "WPS workflow worked",
            },
            task_id="task_lesson",
        )
    )
    orchestrator = OrchestratorAgent()

    recalled = asyncio.run(orchestrator._recall_memory("open attachment in WPS"))

    assert any(item.kind == "lesson" and "workflow.run" in item.content for item in recalled)
