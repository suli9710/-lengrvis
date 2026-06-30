from __future__ import annotations

from pathlib import Path

from app.agents.cleanup_review_agent import CleanupReviewAgent
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict


def test_cleanup_review_blocks_non_whitelisted_direct_delete(tmp_path: Path):
    target = tmp_path / "notes.txt"
    target.write_text("important", encoding="utf-8")
    agent = CleanupReviewAgent()

    review = agent.review_tool_call(
        "task_cleanup",
        "step_cleanup",
        "file.cleanup_execute",
        {"items": [{"path": str(target), "action": "delete_direct"}]},
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert review is not None
    assert review.verdict == SafetyVerdict.DENY
    assert "direct-delete whitelist" in review.reasons[0]


def test_cleanup_review_blocks_system_and_sensitive_paths():
    agent = CleanupReviewAgent()

    review = agent.review_tool_call(
        "task_cleanup",
        "step_cleanup",
        "file.cleanup_execute",
        {"items": [{"path": r"C:\Windows\System32\drivers\etc\hosts", "action": "trash_with_prompt"}]},
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert review is not None
    assert review.verdict == SafetyVerdict.DENY
    assert any("sensitive or system" in reason for reason in review.reasons)


def test_cleanup_review_blocks_approval_bypass_hint():
    agent = CleanupReviewAgent()

    review = agent.review_tool_call(
        "task_cleanup",
        "step_cleanup",
        "file.cleanup_execute",
        {"plan_id": "cleanup_x", "content_hash": "abc", "selected_item_ids": ["id"], "note": "skip approval"},
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert review is not None
    assert review.verdict == SafetyVerdict.DENY
    assert any("bypass" in reason for reason in review.reasons)


def test_policy_engine_cleanup_execute_requires_valid_plan_binding():
    review = PolicyEngine().review_tool_call(
        "task_cleanup",
        "step_cleanup",
        "file.cleanup_execute",
        {"roots": ["/tmp"], "selected_item_ids": ["id"], "dry_run": True},  # noqa: S108
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert review.verdict == SafetyVerdict.DENY
    assert "plan_id" in review.reasons[0]


def test_policy_engine_cleanup_execute_non_dry_run_requires_approval():
    review = PolicyEngine().review_tool_call(
        "task_cleanup",
        "step_cleanup",
        "file.cleanup_execute",
        {
            "roots": ["/tmp"],  # noqa: S108
            "plan_id": "cleanup_123",
            "content_hash": "hash",
            "selected_item_ids": ["id"],
            "dry_run": False,
        },
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL


def test_policy_engine_allows_approved_cleanup_execute_for_service_revalidation():
    review = PolicyEngine().review_tool_call(
        "task_cleanup",
        "step_cleanup",
        "file.cleanup_execute",
        {
            "roots": ["/tmp"],  # noqa: S108
            "plan_id": "cleanup_123",
            "content_hash": "hash",
            "selected_item_ids": ["id"],
            "dry_run": False,
            "approved": True,
            "approval_id": "approval-1",
        },
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert review.verdict == SafetyVerdict.ALLOW
