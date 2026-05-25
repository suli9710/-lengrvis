from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from app.agents.base import BaseAgent


Verdict = Literal["allow", "block"]

RISK_MODEL_BREAK = "risk_model_break"
APPROVAL_BYPASS = "approval_bypass"
WRITE_CONCURRENCY = "write_concurrency"
ORCHESTRATOR_BLOAT = "orchestrator_bloat"
EXTERNAL_SOURCE_COPY = "external_source_copy"
MISSING_FAILURE_TESTS = "missing_failure_tests"

REQUIRED_RISK_CATEGORIES: tuple[str, ...] = (
    RISK_MODEL_BREAK,
    APPROVAL_BYPASS,
    WRITE_CONCURRENCY,
    ORCHESTRATOR_BLOAT,
    EXTERNAL_SOURCE_COPY,
    MISSING_FAILURE_TESTS,
)


class CodeReviewFinding(dict):
    def __init__(
        self,
        *,
        category: str,
        severity: str,
        message: str,
        evidence: list[str],
        recommendation: str,
    ) -> None:
        super().__init__(
            category=category,
            severity=severity,
            message=message,
            evidence=evidence,
            recommendation=recommendation,
        )

    @property
    def category(self) -> str:
        return str(self["category"])

    @property
    def severity(self) -> str:
        return str(self["severity"])

    @property
    def message(self) -> str:
        return str(self["message"])

    @property
    def evidence(self) -> list[str]:
        return list(self["evidence"])

    @property
    def recommendation(self) -> str:
        return str(self["recommendation"])

    def model_dump(self) -> dict[str, Any]:
        return dict(self)


class CodeReviewReport(dict):
    def __init__(self, *, verdict: Verdict, findings: list[CodeReviewFinding], summary: str) -> None:
        super().__init__(verdict=verdict, findings=findings, summary=summary)

    @property
    def verdict(self) -> Verdict:
        return self["verdict"]

    @property
    def findings(self) -> list[CodeReviewFinding]:
        return list(self["findings"])

    @property
    def summary(self) -> str:
        return str(self["summary"])

    def model_dump(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": [finding.model_dump() for finding in self.findings],
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class _ChangedFile:
    path: str
    added_lines: int = 0
    deleted_lines: int = 0
    note: str = ""


class CodeReviewAgent(BaseAgent):
    name = "CodeReviewAgent"
    domain_summary = (
        "Performs deterministic supervisor review of development changes for risk model, approval, "
        "concurrency, orchestration, source-copy, and failure-test risks."
    )
    prompt_file = "code_review_agent.md"

    def allowed_tools(self, registry=None) -> list[str]:
        return []

    def review(
        self,
        changed_files: Iterable[Any] | None,
        review_notes: Any = "",
        test_evidence: Any = None,
        copied_source_flags: Any = None,
    ) -> CodeReviewReport:
        files = [_coerce_changed_file(item) for item in changed_files or ()]
        notes_text = _stringify(review_notes)
        tests_text = _stringify(test_evidence)
        copied_text = _stringify(_truthy_source_flags(copied_source_flags))
        corpus = " ".join(
            part.lower()
            for part in (
                " ".join(file.path for file in files),
                " ".join(file.note for file in files),
                notes_text,
                tests_text,
                copied_text,
            )
            if part
        )

        findings: list[CodeReviewFinding] = []
        findings.extend(self._review_risk_model(files, corpus))
        findings.extend(self._review_approval_bypass(files, corpus))
        findings.extend(self._review_write_concurrency(files, corpus))
        findings.extend(self._review_orchestrator_bloat(files, corpus))
        findings.extend(self._review_external_source_copy(copied_source_flags, corpus))
        findings.extend(self._review_failure_tests(tests_text, corpus))

        verdict: Verdict = "block" if findings else "allow"
        categories = ", ".join(finding.category for finding in findings)
        summary = (
            f"Block: detected {len(findings)} supervisor review risk(s): {categories}."
            if findings
            else "Allow: deterministic supervisor review found no blocking risk categories."
        )
        return CodeReviewReport(verdict=verdict, findings=findings, summary=summary)

    def _review_risk_model(self, files: list[_ChangedFile], corpus: str) -> list[CodeReviewFinding]:
        paths = [
            file.path
            for file in files
            if _path_contains(
                file.path,
                "app/policy/risk.py",
                "app/policy/policy_engine.py",
                "app/core/schemas.py",
                "safety_review_agent.py",
            )
        ]
        if paths or _contains_any(corpus, "risk_model_break", "risk level", "safetyverdict", "risk model"):
            return [
                CodeReviewFinding(
                    category=RISK_MODEL_BREAK,
                    severity="high",
                    message="Change may alter the shared risk model or safety verdict contract.",
                    evidence=paths or _matching_terms(corpus, "risk_model_break", "risk level", "safetyverdict", "risk model"),
                    recommendation="Add compatibility checks and focused tests around risk levels, verdicts, and schema consumers.",
                )
            ]
        return []

    def _review_approval_bypass(self, files: list[_ChangedFile], corpus: str) -> list[CodeReviewFinding]:
        approval_paths = [
            file.path
            for file in files
            if _path_contains(file.path, "routes_approvals.py", "mobile_pairing_service.py", "approval")
        ]
        bypass_terms = _matching_terms(
            corpus,
            "approval_bypass",
            "bypass approval",
            "skip approval",
            "without approval",
            "auto-approve",
            "auto approve",
            "approved=true",
            "requires_approval=false",
            "dry_run=false",
        )
        if bypass_terms or (approval_paths and _contains_any(corpus, "bypass", "skip", "approved=true", "dry_run=false")):
            return [
                CodeReviewFinding(
                    category=APPROVAL_BYPASS,
                    severity="critical",
                    message="Change may execute write or remote actions without an explicit approval gate.",
                    evidence=bypass_terms or approval_paths,
                    recommendation="Require dry-run previews, pending approvals, and approved approval_id checks before execution.",
                )
            ]
        return []

    def _review_write_concurrency(self, files: list[_ChangedFile], corpus: str) -> list[CodeReviewFinding]:
        concurrency_terms = _matching_terms(
            corpus,
            "write_concurrency",
            "parallel write",
            "concurrent write",
            "race condition",
            "no lock",
            "without lock",
            "simultaneous write",
        )
        state_paths = [
            file.path
            for file in files
            if _path_contains(file.path, "state_machine.py", "task_service.py", "core/db.py")
        ]
        if concurrency_terms or (state_paths and _contains_any(corpus, "concurrency", "race", "parallel", "simultaneous")):
            return [
                CodeReviewFinding(
                    category=WRITE_CONCURRENCY,
                    severity="high",
                    message="Change may allow concurrent writes to shared task, plan, approval, or database state.",
                    evidence=concurrency_terms or state_paths,
                    recommendation="Add serialization, idempotency, or locking tests for competing write paths.",
                )
            ]
        return []

    def _review_orchestrator_bloat(self, files: list[_ChangedFile], corpus: str) -> list[CodeReviewFinding]:
        orchestrator_files = [file for file in files if _path_contains(file.path, "orchestrator_agent.py")]
        large_orchestrator_edits = [file.path for file in orchestrator_files if file.added_lines >= 150 or file.deleted_lines >= 150]
        bloat_terms = _matching_terms(
            corpus,
            "orchestrator_bloat",
            "fat orchestrator",
            "orchestrator bloat",
            "directly in orchestrator",
            "large orchestrator",
        )
        if large_orchestrator_edits or bloat_terms:
            return [
                CodeReviewFinding(
                    category=ORCHESTRATOR_BLOAT,
                    severity="medium",
                    message="Change may concentrate domain logic inside the orchestrator instead of a focused module.",
                    evidence=large_orchestrator_edits or bloat_terms,
                    recommendation="Move domain-specific behavior behind a handler, agent, or service boundary with narrow tests.",
                )
            ]
        return []

    def _review_external_source_copy(self, copied_source_flags: Any, corpus: str) -> list[CodeReviewFinding]:
        flags = _truthy_source_flags(copied_source_flags)
        if flags:
            return [
                CodeReviewFinding(
                    category=EXTERNAL_SOURCE_COPY,
                    severity="high",
                    message="Change may include externally copied source without provenance or license clearance.",
                    evidence=flags,
                    recommendation="Replace copied code, document provenance and license, or isolate it behind an approved dependency.",
                )
            ]
        source_terms = _matching_terms(
            corpus,
            "external_source_copy",
            "copied source",
            "external source copy",
            "github gist",
            "stackoverflow",
            "license unknown",
        )
        if source_terms and not _contains_any(
            corpus,
            "clean-room",
            "clean room",
            "no copied source",
            "no direct external source copy",
            "no external source copy",
            "copied_source_flags=[]",
            "copied_source_flags empty",
        ):
            return [
                CodeReviewFinding(
                    category=EXTERNAL_SOURCE_COPY,
                    severity="high",
                    message="Change may include externally copied source without provenance or license clearance.",
                    evidence=source_terms,
                    recommendation="Replace copied code, document provenance and license, or isolate it behind an approved dependency.",
                )
            ]
        return []

    def _review_failure_tests(self, test_evidence: str, corpus: str) -> list[CodeReviewFinding]:
        has_test_run = _contains_any(test_evidence.lower(), "pytest", "passed", "pass", "green")
        has_failure_case = _contains_any(
            test_evidence.lower(),
            "failure",
            "negative",
            "block",
            "deny",
            "error path",
            "exception",
            "regression",
        )
        missing_terms = _matching_terms(corpus, "missing_failure_tests", "happy path only", "no failure test", "not tested")
        if not (has_test_run and has_failure_case) or missing_terms:
            return [
                CodeReviewFinding(
                    category=MISSING_FAILURE_TESTS,
                    severity="medium",
                    message="Review evidence does not show failure-path tests for the change.",
                    evidence=missing_terms or [test_evidence.strip() or "No test evidence supplied."],
                    recommendation="Add or cite pytest coverage for block, failure, denial, or error-path behavior.",
                )
            ]
        return []


def _coerce_changed_file(item: Any) -> _ChangedFile:
    if isinstance(item, _ChangedFile):
        return item
    if isinstance(item, dict):
        return _ChangedFile(
            path=str(item.get("path") or item.get("file") or item.get("name") or ""),
            added_lines=_int_value(item.get("added_lines") or item.get("additions") or item.get("lines_added")),
            deleted_lines=_int_value(item.get("deleted_lines") or item.get("deletions") or item.get("lines_deleted")),
            note=str(item.get("note") or item.get("summary") or item.get("description") or ""),
        )
    return _ChangedFile(path=str(item))


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _path_contains(path: str, *needles: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return any(needle.lower() in normalized for needle in needles)


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _matching_terms(text: str, *terms: str) -> list[str]:
    normalized = text.lower()
    return [term for term in terms if term in normalized]


def _truthy_source_flags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, bool):
        return ["copied_source_flags=True"] if value else []
    if isinstance(value, dict):
        items = value.items()
        return [f"{key}: {val}" for key, val in items if _flag_value_is_truthy(val)]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if _flag_value_is_truthy(item)]
    return [str(value)] if _flag_value_is_truthy(value) else []


def _flag_value_is_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null", "clean"}
    return bool(value)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(f"{key}={_stringify(val)}" for key, val in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify(item) for item in value)
    return str(value)
