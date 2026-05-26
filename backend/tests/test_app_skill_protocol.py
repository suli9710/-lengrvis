from __future__ import annotations

from pathlib import Path

from app.policy.risk import RiskLevel

from app.skills.loader import load_skill_package, review_skill_definition, scan_skill_directories
from app.skills.schemas import SkillDefinition
from app.tools.registry import register_all_tools


def test_skill_definition_accepts_app_target_and_workflow_fields():
    definition = SkillDefinition.model_validate(
        {
            "name": "app-protocol",
            "version": "1.0",
            "agent_owner": "AppAgent",
            "tools": [
                {
                    "name": "skill.app.protocol",
                    "app_target": {
                        "app_id": "wechat.desktop",
                        "display_name": "WeChat Desktop",
                        "interface": "ui_automation",
                        "capabilities": ["focus_window", "click_send"],
                    },
                    "workflow": {
                        "target_app": "wechat.desktop",
                        "action": "send_message",
                        "data_transfer": {"clipboard_text": "message"},
                    },
                    "execution": {"type": "python", "entry": "handlers/intent.py"},
                }
            ],
        }
    )

    tool = definition.tools[0]
    assert tool.app_target is not None
    assert tool.app_target.app_id == "wechat.desktop"
    assert tool.workflow is not None
    assert tool.workflow.target_app == "wechat.desktop"


def test_example_app_skill_packages_load(test_data_dir: Path):
    packages = scan_skill_directories([test_data_dir / "skills"])
    by_name = {package.definition.name: package for package in packages}

    assert "wechat-desktop-message" in by_name
    assert "wps-office-document" in by_name
    assert "windows-settings-workflow" in by_name
    assert by_name["wechat-desktop-message"].definition.tools[0].app_target.app_id == "wechat.desktop"
    assert by_name["wps-office-document"].definition.tools[0].app_target.interface == "com"
    assert by_name["windows-settings-workflow"].definition.effective_risk(
        by_name["windows-settings-workflow"].definition.tools[0]
    ) == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM


def test_example_app_skill_dry_run_handler_returns_intent(test_data_dir: Path):
    package = load_skill_package(test_data_dir / "skills" / "wechat_desktop_message")
    result = package.tool_definitions[0].execute(
        {"contact": "Alice", "message": "hello", "dry_run": True},
        {},
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["intent"]["target_app"] == "wechat.desktop"
    assert result["intent"]["message_length"] == 5


def test_registry_can_register_app_skill_examples(test_data_dir: Path):
    registry = register_all_tools(skill_directories=[str(test_data_dir / "skills")])

    assert registry.get("skill.wechat_desktop.send_message").risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
    assert registry.get("skill.wps_office.open_edit_document").requires_authorized_path is True
    assert registry.get("skill.windows_settings.workflow").supports_dry_run is True


def test_r2_skill_without_dry_run_fails_safety_review(tmp_path: Path):
    entry = tmp_path / "handler.py"
    entry.write_text("print('ok')", encoding="utf-8")
    definition = SkillDefinition.model_validate(
        {
            "name": "unsafe-skill",
            "version": "1.0",
            "agent_owner": "FileAgent",
            "risk": "r2",
            "tools": [
                {
                    "name": "skill.unsafe.write",
                    "execution": {"type": "python", "entry": "handler.py"},
                    "supports_dry_run": False,
                }
            ],
        }
    )

    report = review_skill_definition(definition, tmp_path)

    assert not report.ok
    assert any("dry-run" in issue.message for issue in report.issues)
