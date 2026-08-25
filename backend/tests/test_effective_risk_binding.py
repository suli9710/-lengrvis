from __future__ import annotations

from datetime import UTC, datetime

from app.config import AppSettings
from app.core.schemas import Approval, SafetyReview, ToolCall, approval_ttl_seconds
from app.policy.decision_cache import tool_decision_cache
from app.policy.effective_risk_binding import (
    EFFECTIVE_RISK_BINDING_VERSION,
    approval_risk_binding,
    build_effective_risk_binding,
    effective_risk_binding_error,
    refreshed_effective_risk_error,
)
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools.schemas import ToolDefinition


def _tool(risk: RiskLevel, *, fast_path: bool = False) -> ToolDefinition:
    return ToolDefinition(
        name="test.effective_risk",
        description="effective risk test tool",
        input_schema={},
        output_schema={},
        risk_level=risk,
        agent_owner="TestAgent",
        supports_dry_run=risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM},
        requires_authorized_path=False,
        execute=lambda args, context: {"ok": True},
        read_only=risk == RiskLevel.R0_READ_ONLY,
        concurrency_safe=True,
        effects=["read"]
        if risk == RiskLevel.R0_READ_ONLY
        else ["open"]
        if risk == RiskLevel.R1_OPEN_ONLY
        else ["write"],
        resource_kinds=["test"],
        fast_path_eligible=fast_path,
        trust_tier="builtin",
    )


def _review(risk: RiskLevel, verdict: SafetyVerdict = SafetyVerdict.ALLOW) -> SafetyReview:
    return SafetyReview(
        task_id="task_effective",
        target_type="tool_call",
        verdict=verdict,
        risk_level=risk,
    )


def test_policy_uses_authoritative_now_and_assesses_fast_path_once(monkeypatch) -> None:
    tool = _tool(RiskLevel.R1_OPEN_ONLY, fast_path=True)
    provider_calls = 0

    def authoritative_now() -> datetime:
        nonlocal provider_calls
        provider_calls += 1
        return datetime(2026, 8, 22, 12, 0, tzinfo=UTC)

    engine = PolicyEngine(now_provider=authoritative_now)
    calls = 0
    original = engine.dynamic_risk.assess

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(engine.dynamic_risk, "assess", counted)
    review = engine.review_tool_call(
        "task_effective",
        "step_effective",
        tool.name,
        {},
        tool.risk_level,
        context={"timestamp": "2026-08-22T02:00:00+00:00"},
        tool_definition=tool,
    )

    assert calls == 1
    assert provider_calls == 1
    assert review.risk_level == RiskLevel.R1_OPEN_ONLY
    assert review.declared_risk_level == RiskLevel.R1_OPEN_ONLY


def test_policy_risk_and_cache_bucket_share_one_time_snapshot_at_night_boundary() -> None:
    tool_decision_cache.clear()
    moments = iter(
        [
            datetime(2026, 8, 22, 21, 59, 59, tzinfo=UTC),
            datetime(2026, 8, 22, 22, 0, 0, tzinfo=UTC),
        ]
    )
    engine = PolicyEngine(now_provider=lambda: next(moments))
    tool = _tool(RiskLevel.R1_OPEN_ONLY, fast_path=True)

    before_boundary = engine.review_tool_call(
        "task_time_boundary",
        "step_time_boundary",
        tool.name,
        {},
        tool.risk_level,
        tool_definition=tool,
    )
    after_boundary = engine.review_tool_call(
        "task_time_boundary",
        "step_time_boundary",
        tool.name,
        {},
        tool.risk_level,
        tool_definition=tool,
    )

    assert before_boundary.risk_level == RiskLevel.R1_OPEN_ONLY
    assert after_boundary.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
    assert after_boundary.verdict == SafetyVerdict.NEEDS_USER_APPROVAL
    tool_decision_cache.clear()


def test_dynamic_risk_combines_authoritative_night_and_failures() -> None:
    tool = _tool(RiskLevel.R1_OPEN_ONLY)
    engine = PolicyEngine(now_provider=lambda: datetime(2026, 8, 22, 2, 0, tzinfo=UTC))

    review = engine.review_tool_call(
        "task_effective",
        "step_effective",
        tool.name,
        {},
        tool.risk_level,
        context={"timestamp": "2026-08-22T12:00:00+00:00", "recent_failure_count": 1},
        tool_definition=tool,
    )

    assert review.risk_level == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL


def test_trusted_edits_cannot_auto_clear_dynamic_r3_or_r4() -> None:
    tool = _tool(RiskLevel.R2_REVERSIBLE_MODIFY)
    settings = AppSettings(permission_mode="trusted_edits")
    engine = PolicyEngine(settings=settings, now_provider=lambda: datetime(2026, 8, 22, 2, 0, tzinfo=UTC))

    r3 = engine.review_tool_call(
        "task_effective",
        "step_r3",
        tool.name,
        {"dry_run": False},
        tool.risk_level,
        context={"settings": settings},
        tool_definition=tool,
    )
    r4 = engine.review_tool_call(
        "task_effective",
        "step_r4",
        tool.name,
        {"dry_run": False},
        tool.risk_level,
        context={"settings": settings, "recent_failure_count": 3},
        tool_definition=tool,
    )

    assert (r3.risk_level, r3.verdict) == (
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        SafetyVerdict.NEEDS_USER_APPROVAL,
    )
    assert (r4.risk_level, r4.verdict) == (
        RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
        SafetyVerdict.DENY,
    )


def test_binding_selects_highest_review_and_fails_closed_on_tampering() -> None:
    low = _review(RiskLevel.R2_REVERSIBLE_MODIFY, SafetyVerdict.NEEDS_USER_APPROVAL)
    high = _review(RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, SafetyVerdict.NEEDS_USER_APPROVAL)
    binding = build_effective_risk_binding(RiskLevel.R2_REVERSIBLE_MODIFY, [low, high])

    assert binding == {
        "version": EFFECTIVE_RISK_BINDING_VERSION,
        "declared_risk_level": RiskLevel.R2_REVERSIBLE_MODIFY.value,
        "effective_risk_level": RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value,
        "review_id": high.id,
    }
    assert not effective_risk_binding_error(
        binding,
        current_declared_risk=RiskLevel.R2_REVERSIBLE_MODIFY,
        approval_risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )
    assert effective_risk_binding_error(
        {**binding, "review_id": "tampered"},
        current_declared_risk=RiskLevel.R2_REVERSIBLE_MODIFY,
        approval_risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )
    assert effective_risk_binding_error(
        None,
        current_declared_risk=RiskLevel.R2_REVERSIBLE_MODIFY,
    )


def test_binding_uses_highest_risk_review_id_independent_of_deny_order() -> None:
    lower_deny = _review(RiskLevel.R2_REVERSIBLE_MODIFY, SafetyVerdict.DENY)
    higher_allow = _review(RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, SafetyVerdict.ALLOW)

    forward = build_effective_risk_binding(
        RiskLevel.R2_REVERSIBLE_MODIFY,
        [lower_deny, higher_allow],
    )
    reversed_order = build_effective_risk_binding(
        RiskLevel.R2_REVERSIBLE_MODIFY,
        [higher_allow, lower_deny],
    )

    assert forward == reversed_order
    assert forward["effective_risk_level"] == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value
    assert forward["review_id"] == higher_allow.id


def test_policy_cache_hits_within_authoritative_risk_bucket_and_not_across_night_boundary() -> None:
    tool_decision_cache.clear()
    current_time = [datetime(2026, 8, 22, 12, 0, tzinfo=UTC)]
    engine = PolicyEngine(now_provider=lambda: current_time[0])
    tool = _tool(RiskLevel.R0_READ_ONLY)

    first = engine.review_tool_call(
        "task_effective_cache",
        "step_effective_cache",
        tool.name,
        {},
        tool.risk_level,
        tool_definition=tool,
    )
    current_time[0] = datetime(2026, 8, 22, 12, 5, tzinfo=UTC)
    same_bucket = engine.review_tool_call(
        "task_effective_cache",
        "step_effective_cache",
        tool.name,
        {},
        tool.risk_level,
        tool_definition=tool,
    )
    current_time[0] = datetime(2026, 8, 22, 22, 0, tzinfo=UTC)
    night_bucket = engine.review_tool_call(
        "task_effective_cache",
        "step_effective_cache",
        tool.name,
        {},
        tool.risk_level,
        tool_definition=tool,
    )

    assert first.risk_level == RiskLevel.R0_READ_ONLY
    assert "reused from in-memory cache" in " ".join(same_bucket.reasons).lower()
    assert night_bucket.risk_level == RiskLevel.R1_OPEN_ONLY
    assert "reused from in-memory cache" not in " ".join(night_bucket.reasons).lower()
    tool_decision_cache.clear()


def test_refreshed_risk_requires_preview_only_when_risk_increases() -> None:
    approved = build_effective_risk_binding(
        RiskLevel.R2_REVERSIBLE_MODIFY,
        [_review(RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, SafetyVerdict.NEEDS_USER_APPROVAL)],
    )

    assert not refreshed_effective_risk_error(approved, _review(RiskLevel.R2_REVERSIBLE_MODIFY))
    assert refreshed_effective_risk_error(
        approved,
        _review(RiskLevel.R4_FORBIDDEN_OR_HANDOFF, SafetyVerdict.DENY),
    )


def test_approval_ttl_and_tool_call_use_effective_risk_with_legacy_defaults() -> None:
    binding = build_effective_risk_binding(
        RiskLevel.R2_REVERSIBLE_MODIFY,
        [_review(RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, SafetyVerdict.NEEDS_USER_APPROVAL)],
    )
    approval = Approval(
        task_id="task_effective",
        message="approve",
        risk_level=binding["effective_risk_level"],
        engineering_boundary={"risk_provenance": binding},
    )
    call = ToolCall.model_validate(
        {
            "task_id": "task_effective",
            "step_id": "step_effective",
            "tool_name": "test.effective_risk",
            "risk_level": RiskLevel.R2_REVERSIBLE_MODIFY,
        }
    )

    assert approval_ttl_seconds(approval.risk_level) == 5 * 60
    assert approval_risk_binding(approval) == binding
    assert call.declared_risk_level is None
    assert call.risk_review_id == ""
    assert call.risk_binding_version == ""
