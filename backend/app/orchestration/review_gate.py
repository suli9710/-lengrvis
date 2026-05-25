from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from app.agents.code_review_agent import CodeReviewAgent, CodeReviewReport


class ReviewGateStatus(StrEnum):
    OPEN = "open"
    BLOCKED = "blocked"
    PASSED = "passed"


@dataclass(slots=True)
class ReviewGateReport:
    stage: str
    status: ReviewGateStatus
    supervisor_report: CodeReviewReport
    unresolved_findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def can_advance(self) -> bool:
        return self.status == ReviewGateStatus.PASSED

    def model_dump(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "can_advance": self.can_advance,
            "supervisor_report": self.supervisor_report.model_dump(),
            "unresolved_findings": list(self.unresolved_findings),
        }


class SupervisorReviewGate:
    """Development-time gate: blocked review findings must close before the next phase."""

    def __init__(self, reviewer: CodeReviewAgent | None = None) -> None:
        self.reviewer = reviewer or CodeReviewAgent()

    def review_stage(
        self,
        *,
        stage: str,
        changed_files: Iterable[Any],
        review_notes: Any = "",
        test_evidence: Any = None,
        copied_source_flags: Any = None,
    ) -> ReviewGateReport:
        report = self.reviewer.review(
            changed_files=changed_files,
            review_notes=review_notes,
            test_evidence=test_evidence,
            copied_source_flags=copied_source_flags,
        )
        unresolved = [finding.model_dump() for finding in report.findings]
        status = ReviewGateStatus.PASSED if report.verdict == "allow" else ReviewGateStatus.BLOCKED
        return ReviewGateReport(
            stage=stage,
            status=status,
            supervisor_report=report,
            unresolved_findings=unresolved,
        )
