from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.core import db
from app.policy.permissions import PermissionPolicy, PermissionRule
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


def _tool(name: str = "test.safe_read", *, risk: RiskLevel = RiskLevel.R0_READ_ONLY, trust: str = "builtin") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        input_schema={},
        output_schema={},
        risk_level=risk,
        agent_owner="TestAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=lambda args, context: {"ok": True},
        effects=["read"],
        resource_kinds=["test"],
        fast_path_eligible=True,
        trust_tier=trust,
    )


def test_default_tool_definition_metadata_is_not_fast_pathable():
    tool = ToolDefinition(
        name="third.party.read",
        description="third party read",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="ThirdParty",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=lambda args, context: {"ok": True},
    )

    review = PolicyEngine().review_tool_call(
        "task_fast",
        "step_fast",
        tool.name,
        {},
        tool.risk_level,
        tool_definition=tool,
    )

    assert tool.fast_path_eligible is False
    assert tool.trust_tier == "unknown"
    assert review.verdict == SafetyVerdict.ALLOW
    assert "fast path" not in " ".join(review.reasons).lower()


def test_r0_registry_tool_can_use_deterministic_fast_path():
    review = PolicyEngine().review_tool_call(
        "task_fast",
        "step_fast",
        "system.get_info",
        {},
        RiskLevel.R0_READ_ONLY,
        tool_definition=_tool("system.get_info"),
    )

    assert review.verdict == SafetyVerdict.ALLOW
    assert "fast path" in " ".join(review.reasons).lower()


def test_permission_deny_preempts_fast_path():
    policy = PermissionPolicy(
        rules=[
            PermissionRule(
                id="deny_read",
                effect="deny",
                tools=["system.get_info"],
                reason="No system reads now.",
            )
        ]
    )

    review = PolicyEngine(permission_policy=policy).review_tool_call(
        "task_fast",
        "step_fast",
        "system.get_info",
        {},
        RiskLevel.R0_READ_ONLY,
        tool_definition=_tool("system.get_info"),
    )

    assert review.verdict == SafetyVerdict.DENY
    assert "deny_read" in review.reasons[0]


def test_system_path_falls_back_to_dynamic_risk_not_fast_path():
    review = PolicyEngine().review_tool_call(
        "task_fast",
        "step_fast",
        "file.list_directory",
        {"path": r"C:\Windows\System32"},
        RiskLevel.R0_READ_ONLY,
        tool_definition=_tool("file.list_directory"),
    )

    assert review.risk_level != RiskLevel.R0_READ_ONLY
    assert "fast path" not in " ".join(review.reasons).lower()


def test_sensitive_args_do_not_fast_path():
    review = PolicyEngine().review_tool_call(
        "task_fast",
        "step_fast",
        "system.get_info",
        {"note": "token abc1234567890"},
        RiskLevel.R0_READ_ONLY,
        tool_definition=_tool("system.get_info"),
    )

    assert review.verdict in {SafetyVerdict.ALLOW, SafetyVerdict.DENY}
    assert "fast path" not in " ".join(review.reasons).lower()


def test_skill_advisory_metadata_does_not_fast_path():
    review = PolicyEngine().review_tool_call(
        "task_fast",
        "step_fast",
        "skill.safe_read",
        {},
        RiskLevel.R0_READ_ONLY,
        tool_definition=_tool("skill.safe_read", trust="skill"),
    )

    assert review.verdict == SafetyVerdict.ALLOW
    assert "fast path" not in " ".join(review.reasons).lower()


def test_external_network_metadata_does_not_fast_path():
    tool = _tool("browser.open_url", risk=RiskLevel.R1_OPEN_ONLY)
    tool.effects = ["open"]
    tool.external_network = True

    review = PolicyEngine().review_tool_call(
        "task_fast",
        "step_fast",
        tool.name,
        {"url": "https://example.com"},
        tool.risk_level,
        tool_definition=tool,
    )

    assert review.verdict == SafetyVerdict.ALLOW
    assert "fast path" not in " ".join(review.reasons).lower()


def test_cache_key_includes_dynamic_context():
    engine = PolicyEngine(now_provider=lambda: datetime.fromisoformat("2026-05-26T12:00:00+00:00"))
    clean = engine.review_tool_call(
        "task_fast",
        "step_fast",
        "system.get_info",
        {},
        RiskLevel.R0_READ_ONLY,
        context={"recent_failure_count": 0},
        tool_definition=_tool("system.get_info"),
    )
    risky = engine.review_tool_call(
        "task_fast",
        "step_fast",
        "system.get_info",
        {},
        RiskLevel.R0_READ_ONLY,
        context={"recent_failure_count": 3},
        tool_definition=_tool("system.get_info"),
    )

    assert "fast path" in " ".join(clean.reasons).lower()
    assert risky.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
