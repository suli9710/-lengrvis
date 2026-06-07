from __future__ import annotations

from pathlib import Path

import pytest

from app.policy.risk import RiskLevel

from app.skills.loader import load_skill_package, review_skill_definition, scan_skill_directories
from app.skills.schemas import LEGACY_PERMISSION, SkillDefinition, SkillLoadError
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


def test_product_manifest_fields_permissions_schema_paths_and_smoke_metadata_load(tmp_path: Path):
    skill_root = tmp_path / "product_skill"
    skill_root.mkdir()
    (skill_root / "handlers").mkdir()
    (skill_root / "schemas").mkdir()
    (skill_root / "handlers" / "send.py").write_text("print('{\"ok\": true}')\n", encoding="utf-8")
    (skill_root / "schemas" / "input.json").write_text(
        '{"type":"object","properties":{"message":{"type":"string"}},"required":["message"]}',
        encoding="utf-8",
    )
    (skill_root / "schemas" / "output.yaml").write_text(
        "type: object\nproperties:\n  ok:\n    type: boolean\n",
        encoding="utf-8",
    )
    (skill_root / "skill.yaml").write_text(
        """
name: product-skill
version: "1.2.3"
agent_owner: AppAgent
risk:
  default: R2_REVERSIBLE_MODIFY
permissions:
  - messaging.read
tools:
  - name: skill.product.send_message
    description: Send a message through a product manifest fixture.
    risk: r2
    permissions:
      - messaging.send
    supports_dry_run: true
    entrypoint: handlers/send.py
    input_schema_path: schemas/input.json
    output_schema_path: schemas/output.yaml
    smoke_tests:
      - name: dry-run
        args:
          message: hello
          dry_run: true
        expected:
          ok: true
    rollback_hint: Message send cannot be automatically undone; hand off to the user for deletion if needed.
    execution:
      type: python
      entry: handlers/send.py
""".strip(),
        encoding="utf-8",
    )

    package = load_skill_package(skill_root)
    tool = package.definition.tools[0]

    assert package.safety_report.ok is True
    assert package.safety_report.issues == []
    assert package.definition.risk == RiskLevel.R2_REVERSIBLE_MODIFY
    assert package.definition.effective_permissions(tool) == ["messaging.send"]
    assert tool.entrypoint == "handlers/send.py"
    assert tool.input_schema["required"] == ["message"]
    assert tool.output_schema["properties"]["ok"]["type"] == "boolean"
    assert tool.smoke_tests[0].name == "dry-run"
    assert package.tool_definitions[0].capabilities == ["messaging.send"]
    assert "send" in package.tool_definitions[0].effects
    assert "message" in package.tool_definitions[0].resource_kinds


def test_legacy_manifest_missing_permissions_loads_with_warning(tmp_path: Path):
    skill_root = tmp_path / "legacy_skill"
    skill_root.mkdir()
    (skill_root / "handler.py").write_text("print('{\"ok\": true}')\n", encoding="utf-8")
    (skill_root / "skill.yaml").write_text(
        """
name: legacy-skill
version: "1.0"
agent_owner: FileAgent
tools:
  - name: skill.legacy.echo
    execution:
      type: python
      entry: handler.py
""".strip(),
        encoding="utf-8",
    )

    package = load_skill_package(skill_root)

    assert package.definition.effective_permissions(package.definition.tools[0]) == [LEGACY_PERMISSION]
    assert package.safety_report.ok is True
    assert any(issue.severity == "warning" and "permissions missing" in issue.message for issue in package.safety_report.issues)


def test_invalid_manifest_risk_is_rejected(tmp_path: Path):
    skill_root = tmp_path / "bad_risk"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """
name: bad-risk
version: "1.0"
agent_owner: FileAgent
risk:
  default: probably_safe
tools:
  - name: skill.bad.risk
    execution:
      type: python
      entry: handler.py
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(SkillLoadError, match="unsupported risk level"):
        load_skill_package(skill_root)


def test_schema_path_traversal_is_rejected_before_install(tmp_path: Path):
    skill_root = tmp_path / "escape_schema"
    skill_root.mkdir()
    (skill_root / "handler.py").write_text("print('{\"ok\": true}')\n", encoding="utf-8")
    (tmp_path / "outside.json").write_text('{"type":"object"}', encoding="utf-8")
    (skill_root / "skill.yaml").write_text(
        """
name: escape-schema
version: "1.0"
agent_owner: FileAgent
permissions:
  - filesystem.read
tools:
  - name: skill.escape.schema
    input_schema_path: ../outside.json
    execution:
      type: python
      entry: handler.py
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(SkillLoadError, match="path traversal"):
        load_skill_package(skill_root)


def test_high_risk_permission_requires_smoke_and_rollback_metadata(tmp_path: Path):
    skill_root = tmp_path / "delete_skill"
    skill_root.mkdir()
    (skill_root / "handler.py").write_text("print('{\"ok\": true}')\n", encoding="utf-8")
    (skill_root / "skill.yaml").write_text(
        """
name: delete-skill
version: "1.0"
agent_owner: FileAgent
risk: R3_DESTRUCTIVE_OR_SYSTEM
permissions:
  - filesystem.delete
tools:
  - name: skill.delete.file
    supports_dry_run: true
    execution:
      type: python
      entry: handler.py
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(SkillLoadError, match="smoke_tests|rollback_hint"):
        load_skill_package(skill_root)


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
    package = load_skill_package(
        test_data_dir / "skills" / "wechat_desktop_message",
        allow_unsafe_local_skill_execution=True,
    )
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
