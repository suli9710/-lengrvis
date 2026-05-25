from __future__ import annotations

from app.orchestration.review_gate import ReviewGateStatus, SupervisorReviewGate


def test_supervisor_review_gate_allows_stage_with_clean_review_evidence():
    gate = SupervisorReviewGate()

    report = gate.review_stage(
        stage="ToolRuntime",
        changed_files=[
            "backend/app/orchestration/tool_runtime.py",
            "backend/tests/test_tool_runtime.py",
        ],
        review_notes="Scoped runtime extraction with approval gate preserved.",
        test_evidence=[
            "pytest backend/tests/test_tool_runtime.py passed",
            "negative failure test covers validation denial and blocked output paths",
        ],
        copied_source_flags=[],
    )

    assert report.status == ReviewGateStatus.PASSED
    assert report.can_advance is True
    assert report.unresolved_findings == []
    assert report.model_dump()["can_advance"] is True


def test_supervisor_review_gate_blocks_stage_with_unresolved_findings():
    gate = SupervisorReviewGate()

    report = gate.review_stage(
        stage="Approval",
        changed_files=[{"path": "backend/app/api/routes_approvals.py", "added_lines": 25}],
        review_notes="dry_run=False approved=True without approval_id; no failure test",
        test_evidence="pytest smoke passed; happy path only",
        copied_source_flags=[],
    )

    assert report.status == ReviewGateStatus.BLOCKED
    assert report.can_advance is False
    assert report.unresolved_findings
    assert report.model_dump()["status"] == "blocked"
