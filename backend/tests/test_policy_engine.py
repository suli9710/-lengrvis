from __future__ import annotations

import importlib

import pytest

from app.config import AppSettings
from app.core.schemas import ToolResult
from app.policy.policy_engine import PolicyEngine
from app.policy.policy_rules import BROWSER_CONTENT_PROMPT_INJECTION_WARNING, BROWSER_CONTENT_TRUST
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools.registry import register_all_tools
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _register_builtin_tools():
    register_all_tools(load_skills=False)


LEGACY_STUB_MODULES = (
    "backend.policy.engine",
    "backend.security.paths",
    "backend.providers.fallback",
    "backend.tools.files",
)


def _trusted_read_tool() -> ToolDefinition:
    return ToolDefinition(
        name="file.read_text",
        description="Read authorized text files.",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="FileAgent",
        supports_dry_run=False,
        requires_authorized_path=True,
        execute=lambda _args, _context: {},
        capabilities=["file.read"],
        effects=["read"],
        resource_kinds=["file"],
        fast_path_eligible=True,
        trust_tier="builtin",
    )


def _trusted_reversible_tool() -> ToolDefinition:
    return ToolDefinition(
        name="file.edit_text",
        description="Edit authorized text files.",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=True,
        execute=lambda _args, _context: {},
        read_only=False,
        concurrency_safe=False,
        destructive=False,
        effects=["write"],
        resource_kinds=["file"],
        trust_tier="builtin",
    )


def test_policy_engine_uses_real_app_contract():
    assert PolicyEngine.__module__ == "app.policy.policy_engine"


def test_legacy_stub_contract_modules_are_not_importable():
    for module_name in LEGACY_STUB_MODULES:
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            assert exc.name in {module_name, module_name.rsplit(".", 1)[0]}
        else:
            raise AssertionError(f"{module_name} should not shadow app.* contracts")


def test_policy_allows_trusted_read_only_tool_call():
    review = PolicyEngine().review_tool_call(
        task_id="task_contract",
        step_id="step_read",
        tool_name="file.read_text",
        args={"path": "notes/safe.txt"},
        risk_level=RiskLevel.R0_READ_ONLY,
        context={"user_trust_level": "medium"},
        tool_definition=_trusted_read_tool(),
    )

    assert review.verdict == SafetyVerdict.ALLOW
    assert review.risk_level == RiskLevel.R0_READ_ONLY


def test_policy_requires_approval_for_modifying_file_tool_call():
    review = PolicyEngine().review_tool_call(
        task_id="task_contract",
        step_id="step_write",
        tool_name="file.write_text",
        args={"path": "notes/safe.txt", "text": "updated", "dry_run": False},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        context={"user_trust_level": "medium"},
    )

    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL
    assert review.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY


def test_classify_unknown_tool_is_fail_closed():
    assert PolicyEngine().classify_tool_name("totally.unknown.tool") == RiskLevel.R4_FORBIDDEN_OR_HANDOFF


def test_policy_denies_forbidden_shell_or_secret_tool_call():
    review = PolicyEngine().review_tool_call(
        task_id="task_contract",
        step_id="step_shell",
        tool_name="shell.exec",
        args={"command": "Get-Content token.txt"},
        risk_level=RiskLevel.R0_READ_ONLY,
        context={"user_trust_level": "medium"},
    )

    assert review.verdict == SafetyVerdict.DENY
    assert review.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF


def test_policy_reviews_tool_result_summary_when_tool_declares_summarizer():
    tool = ToolDefinition(
        name="developer.lengrvis_code",
        description="Run a trusted developer subprocess.",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R1_OPEN_ONLY,
        agent_owner="DeveloperExecutionEngine",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=lambda _args, _context: {},
        read_only=False,
        concurrency_safe=False,
        effects=["read", "execute_subprocess"],
        resource_kinds=["workspace", "developer_runtime"],
        trust_tier="builtin",
        result_summary=lambda output: str(output.get("assistant_text") or "completed"),
    )
    result = ToolResult(
        tool_call_id="tool_dev",
        ok=True,
        output={
            "assistant_text": "Repository inspection completed.",
            "runtime_health": {"command": ["python", "fake.py"], "token_metadata": "internal key name"},
        },
    )

    review = PolicyEngine().review_tool_result(
        "task_contract",
        "step_dev",
        tool.name,
        result,
        tool.risk_level,
        tool_definition=tool,
    )

    assert review.verdict == SafetyVerdict.ALLOW


def test_policy_denies_browser_tool_result_with_prompt_injection_warning():
    result = ToolResult(
        tool_call_id="tool_browser_injection",
        ok=True,
        output={
            "ok": True,
            "text": "Visible page text remains available in storage.",
            "content_trust": BROWSER_CONTENT_TRUST,
            "browser_content_warnings": [BROWSER_CONTENT_PROMPT_INJECTION_WARNING],
        },
    )

    review = PolicyEngine().review_tool_result(
        "task_browser",
        "step_browser",
        "browser.read_page",
        result,
        RiskLevel.R0_READ_ONLY,
    )

    assert review.verdict == SafetyVerdict.DENY
    assert review.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF
    assert "prompt-injection" in " ".join(review.reasons)


def test_policy_allows_browser_tool_result_with_untrusted_label_without_warning():
    result = ToolResult(
        tool_call_id="tool_browser_normal",
        ok=True,
        output={
            "ok": True,
            "text": "Ordinary visible page text.",
            "content_trust": BROWSER_CONTENT_TRUST,
        },
    )

    review = PolicyEngine().review_tool_result(
        "task_browser",
        "step_browser",
        "browser.read_page",
        result,
        RiskLevel.R0_READ_ONLY,
    )

    assert review.verdict == SafetyVerdict.ALLOW
    assert "untrusted browser content" in " ".join(review.reasons)


def test_permission_mode_plan_denies_modifying_tool_call():
    tool = _trusted_reversible_tool()
    review = PolicyEngine(settings=AppSettings(permission_mode="plan")).review_tool_call(
        task_id="task_modes",
        step_id="step_plan",
        tool_name=tool.name,
        args={"path": "notes/safe.txt", "text": "updated", "dry_run": False},
        risk_level=tool.risk_level,
        context={"settings": AppSettings(permission_mode="plan")},
        tool_definition=tool,
    )

    assert review.verdict == SafetyVerdict.DENY
    assert "plan" in " ".join(review.reasons)


def test_permission_mode_dont_ask_denies_approval_requiring_tool_call():
    tool = _trusted_reversible_tool()
    review = PolicyEngine(settings=AppSettings(permission_mode="dont_ask")).review_tool_call(
        task_id="task_modes",
        step_id="step_dont_ask",
        tool_name=tool.name,
        args={"path": "notes/safe.txt", "text": "updated", "dry_run": False},
        risk_level=tool.risk_level,
        context={"settings": AppSettings(permission_mode="dont_ask")},
        tool_definition=tool,
    )

    assert review.verdict == SafetyVerdict.DENY
    assert "dont_ask" in " ".join(review.reasons)


def test_permission_mode_trusted_edits_allows_trusted_reversible_tool_call():
    tool = _trusted_reversible_tool()
    review = PolicyEngine(settings=AppSettings(permission_mode="trusted_edits")).review_tool_call(
        task_id="task_modes",
        step_id="step_trusted",
        tool_name=tool.name,
        args={"path": "notes/safe.txt", "text": "updated", "dry_run": False},
        risk_level=tool.risk_level,
        context={"settings": AppSettings(permission_mode="trusted_edits")},
        tool_definition=tool,
    )

    assert review.verdict == SafetyVerdict.ALLOW
    assert "trusted_edits" in " ".join(review.reasons)


def test_permission_mode_trusted_edits_does_not_auto_allow_local_test_execution():
    tool = ToolDefinition(
        name="dev.test_run",
        description="Run controlled local tests.",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="ComputerAgent",
        supports_dry_run=True,
        requires_authorized_path=True,
        execute=lambda _args, _context: {},
        read_only=False,
        concurrency_safe=False,
        destructive=False,
        effects=["read", "inspect", "execute_test"],
        resource_kinds=["workspace", "repository"],
        trust_tier="builtin",
    )

    review = PolicyEngine(settings=AppSettings(permission_mode="trusted_edits")).review_tool_call(
        task_id="task_modes",
        step_id="step_tests",
        tool_name=tool.name,
        args={"command": "pytest backend/tests", "dry_run": False},
        risk_level=tool.risk_level,
        context={"settings": AppSettings(permission_mode="trusted_edits")},
        tool_definition=tool,
    )

    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL
    assert "approval" in " ".join(review.reasons).lower()


def test_permission_mode_default_still_requires_approval_for_write():
    tool = _trusted_reversible_tool()
    review = PolicyEngine(settings=AppSettings(permission_mode="default")).review_tool_call(
        task_id="task_modes",
        step_id="step_default",
        tool_name=tool.name,
        args={"path": "notes/safe.txt", "text": "updated", "dry_run": False},
        risk_level=tool.risk_level,
        context={"settings": AppSettings(permission_mode="default")},
        tool_definition=tool,
    )

    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL
