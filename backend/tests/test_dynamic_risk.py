from __future__ import annotations

from datetime import datetime

import pytest

from app.policy.dynamic_risk import DynamicRiskAssessor
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RISK_ORDER, RiskLevel, SafetyVerdict
from app.tools.schemas import ToolDefinition


def _builtin_file_tool(name: str, risk: RiskLevel) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        input_schema={},
        output_schema={},
        risk_level=risk,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=True,
        execute=lambda args, context: {"ok": True},
        effects=["read"] if risk == RiskLevel.R0_READ_ONLY else ["delete"],
        resource_kinds=["file"],
        trust_tier="builtin",
        fast_path_eligible=risk == RiskLevel.R0_READ_ONLY,
    )


def test_same_tool_has_different_risk_for_user_document_and_system_path():
    policy = PolicyEngine()

    user_doc_review = policy.review_tool_call(
        "task_dynamic",
        "step_user_doc",
        "file.list_directory",
        {"path": r"C:\Users\Suli\Documents\notes", "dry_run": True},
        RiskLevel.R0_READ_ONLY,
        tool_definition=_builtin_file_tool("file.list_directory", RiskLevel.R0_READ_ONLY),
    )
    system_review = policy.review_tool_call(
        "task_dynamic",
        "step_system",
        "file.list_directory",
        {"path": r"C:\Windows\System32", "dry_run": True},
        RiskLevel.R0_READ_ONLY,
        tool_definition=_builtin_file_tool("file.list_directory", RiskLevel.R0_READ_ONLY),
    )

    assert user_doc_review.risk_level == RiskLevel.R0_READ_ONLY
    assert user_doc_review.verdict == SafetyVerdict.ALLOW
    assert system_review.risk_level != user_doc_review.risk_level
    assert RISK_ORDER[system_review.risk_level] > RISK_ORDER[user_doc_review.risk_level]


def test_policy_engine_applies_static_tool_risk_before_dynamic_adjustments():
    review = PolicyEngine().review_tool_call(
        "task_dynamic",
        "step_static",
        "file.trash",
        {"path": r"C:\Users\Suli\Documents\old.txt", "dry_run": True},
        RiskLevel.R0_READ_ONLY,
        tool_definition=_builtin_file_tool("file.trash", RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM),
    )

    assert review.risk_level == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL


def test_deep_night_context_upgrades_static_open_risk():
    assessment = DynamicRiskAssessor().assess(
        tool_name="browser.open_url",
        args={"url": "https://example.com"},
        base_risk=RiskLevel.R1_OPEN_ONLY,
        context={"timestamp": datetime(2026, 5, 26, 2, 30)},
    )

    assert assessment.adjusted_risk == RiskLevel.R2_REVERSIBLE_MODIFY
    assert any("Deep-night" in reason for reason in assessment.reasons)


@pytest.mark.parametrize("recent_failure_count,expected", [(1, RiskLevel.R1_OPEN_ONLY), (3, RiskLevel.R2_REVERSIBLE_MODIFY)])
def test_recent_failures_escalate_by_failure_volume(recent_failure_count: int, expected: RiskLevel):
    assessment = DynamicRiskAssessor().assess(
        tool_name="system.get_info",
        args={},
        base_risk=RiskLevel.R0_READ_ONLY,
        context={"recent_failure_count": recent_failure_count},
    )

    assert assessment.adjusted_risk == expected


def test_user_trust_can_reduce_only_contextual_escalation():
    assessor = DynamicRiskAssessor()

    low_trust = assessor.assess(
        tool_name="app.open_file",
        args={"path": r"C:\Users\Suli\Documents\report.docx"},
        base_risk=RiskLevel.R1_OPEN_ONLY,
        context={"user_trust_level": "guest"},
    )
    high_trust_after_failures = assessor.assess(
        tool_name="app.open_file",
        args={"path": r"C:\Users\Suli\Documents\report.docx"},
        base_risk=RiskLevel.R1_OPEN_ONLY,
        context={"recent_failure_count": 1, "user_trust_level": "trusted"},
    )
    trusted_static_write = assessor.assess(
        tool_name="file.write_text",
        args={"path": r"C:\Users\Suli\Documents\report.txt"},
        base_risk=RiskLevel.R2_REVERSIBLE_MODIFY,
        context={"user_trust_level": "trusted"},
    )

    assert low_trust.adjusted_risk == RiskLevel.R2_REVERSIBLE_MODIFY
    assert high_trust_after_failures.adjusted_risk == RiskLevel.R1_OPEN_ONLY
    assert trusted_static_write.adjusted_risk == RiskLevel.R2_REVERSIBLE_MODIFY
