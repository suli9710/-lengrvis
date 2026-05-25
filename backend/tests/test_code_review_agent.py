from __future__ import annotations

from app.agents.code_review_agent import (
    APPROVAL_BYPASS,
    EXTERNAL_SOURCE_COPY,
    MISSING_FAILURE_TESTS,
    ORCHESTRATOR_BLOAT,
    RISK_MODEL_BREAK,
    WRITE_CONCURRENCY,
    CodeReviewAgent,
    REQUIRED_RISK_CATEGORIES,
)


def _categories(report):
    return {finding.category for finding in report.findings}


def test_code_review_agent_allows_scoped_change_with_failure_test_evidence():
    agent = CodeReviewAgent()

    report = agent.review(
        changed_files=[
            "backend/app/agents/code_review_agent.py",
            "backend/app/llm/prompts/code_review_agent.md",
            "backend/tests/test_code_review_agent.py",
        ],
        review_notes="Adds deterministic supervisor review only; no tools, provider calls, or writes.",
        test_evidence=[
            "pytest backend/tests/test_code_review_agent.py passed",
            "Includes negative failure tests for block verdict and external source flags.",
        ],
        copied_source_flags=[],
    )

    assert report.verdict == "allow"
    assert report.findings == []
    assert report.model_dump()["verdict"] == "allow"
    assert agent.allowed_tools() == []
    assert "CodeReviewAgent" in agent.system_prompt()


def test_code_review_agent_blocks_all_required_supervisor_risks():
    agent = CodeReviewAgent()

    report = agent.review(
        changed_files=[
            {"path": "backend/app/policy/risk.py", "added_lines": 12},
            {"path": "backend/app/api/routes_approvals.py", "added_lines": 30},
            {"path": "backend/app/orchestration/state_machine.py", "added_lines": 45},
            {"path": "backend/app/agents/orchestrator_agent.py", "added_lines": 220},
        ],
        review_notes=(
            "Risk model update sets dry_run=False and approved=True to skip approval. "
            "It introduces a race condition from parallel write paths with no lock. "
            "Large orchestrator bloat was added directly in orchestrator. "
            "Tests are happy path only; no failure test was added."
        ),
        test_evidence="pytest backend/tests/test_smoke.py passed; happy path only",
        copied_source_flags=[
            {"path": "backend/app/agents/orchestrator_agent.py", "source": "GitHub gist", "license": "license unknown"}
        ],
    )

    assert report.verdict == "block"
    assert _categories(report) == set(REQUIRED_RISK_CATEGORIES)
    assert RISK_MODEL_BREAK in _categories(report)
    assert APPROVAL_BYPASS in _categories(report)
    assert WRITE_CONCURRENCY in _categories(report)
    assert ORCHESTRATOR_BLOAT in _categories(report)
    assert EXTERNAL_SOURCE_COPY in _categories(report)
    assert MISSING_FAILURE_TESTS in _categories(report)
    assert "Block:" in report.summary


def test_code_review_agent_does_not_flag_clean_room_no_copy_note():
    agent = CodeReviewAgent()

    report = agent.review(
        changed_files=["backend/app/orchestration/tool_runtime.py"],
        review_notes="Clean-room implementation with no direct external source copy.",
        test_evidence=[
            "pytest backend/tests/test_tool_runtime.py passed",
            "negative failure tests cover denied and error path behavior",
        ],
        copied_source_flags=[],
    )

    assert EXTERNAL_SOURCE_COPY not in _categories(report)
