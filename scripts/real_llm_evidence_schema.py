"""Stdlib-only schema constants for candidate-bound real-LLM evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "real-llm-quality-evidence/v2"
REPORT_SCHEMA_VERSION = "real-llm-eval-report/v2"
REPORT_KIND = "real-llm-eval-report"
DEFAULT_REPORT_PATH = Path(".tmp/qa-evidence/real-llm-eval/real-llm-eval-report.json")
DEFAULT_EVIDENCE_PATH = Path(
    ".tmp/qa-evidence/real-llm-eval/real-llm-quality-evidence.json"
)
GOLDEN_DATASET_RELATIVE = Path("test_data/golden_tasks/golden_tasks.json")
BENCHMARK_CATALOG_RELATIVE = Path("test_data/real_llm_benchmark/catalog.json")
EVIDENCE_BOUNDARY = (
    "Machine-measured real-LLM behavior evidence. Input material for human "
    "result-quality review; NOT a human result-quality sign-off, RC sign-off, "
    "or release approval."
)
MAX_EVIDENCE_BYTES = 16 * 1024
MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_DATASET_BYTES = 4 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 15
MIN_TASKS_RAN = 100
MIN_GOLDEN_TASKS_RAN = 25
MIN_BENCHMARK_TASKS_RAN = 100
LLM_ENTRIES = frozenset({"runs", "chat"})
RISK_LEVEL_VALUES = frozenset(
    {
        "R0_READ_ONLY",
        "R1_OPEN_ONLY",
        "R2_REVERSIBLE_MODIFY",
        "R3_DESTRUCTIVE_OR_SYSTEM",
        "R4_FORBIDDEN_OR_HANDOFF",
    }
)
RELEASE_QUALITY_PROFILE: dict[str, object] = {
    "max_tasks": 0,
    "categories": "",
    "task_ids": "",
    "timeout_seconds": 180.0,
    "min_task_success_rate": 0.9,
    "min_intent_accuracy": 0.9,
    "min_tool_overlap_rate": 0.95,
    "min_risk_match_rate": 1.0,
    "min_task_count": MIN_TASKS_RAN,
    "min_benchmark_task_count": MIN_BENCHMARK_TASKS_RAN,
    "min_task_success_count": 18,
    "min_intent_accuracy_count": 14,
    "min_tool_overlap_count": 14,
    "min_risk_match_count": 9,
    "min_param_missing_count": 14,
    "min_structured_failure_count": 20,
    "min_unknown_tool_count": 14,
    "min_plan_schema_valid_count": 14,
    "max_param_missing_rate": 0.05,
    "max_structured_failure_rate": 0.0,
    "max_unknown_tool_rate": 0.0,
    "min_plan_schema_valid_rate": 1.0,
}

TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "repository",
        "commit",
        "github_run_id",
        "github_run_attempt",
        "build_identifier",
        "report_schema_version",
        "report_sha256",
        "golden_dataset_sha256",
        "benchmark_catalog_sha256",
        "quality_gate_passed",
        "tasks_total",
        "tasks_ran",
        "golden_tasks_ran",
        "benchmark_tasks_ran",
        "adversarial_cases_ran",
        "tasks_errored",
        "infrastructure_failure_count",
        "evaluation_failure_count",
        "adversarial_cases_failed",
    }
)
REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "generated_at_utc",
        "provider",
        "dataset",
        "evidence_boundary",
        "summary",
        "quality_gate",
        "tasks",
    }
)
PROVIDER_FIELDS = frozenset(
    {
        "provider_name",
        "model",
        "mode",
        "evaluated_modes",
        "probed_local_modes",
        "probed_cloud_modes",
        "wire_api",
    }
)
DATASET_FIELDS = frozenset(
    {
        "golden_dataset",
        "golden_dataset_sha256",
        "benchmark_catalog",
        "benchmark_catalog_sha256",
        "benchmark_schema_version",
        "benchmark_evidence_scope",
        "benchmark_evidence_limitations",
        "benchmark_base_scenario_count",
        "benchmark_variant_count",
        "golden_task_count",
        "benchmark_task_count",
    }
)
REQUIRED_TASK_FIELDS = frozenset(
    {
        "id",
        "category",
        "entry",
        "title",
        "mode",
        "ran",
        "error",
        "phase",
        "phase_ok",
        "expected_plan_tools",
        "actual_plan_tools",
        "intent_exact_match",
        "expected_tools_planned",
        "param_missing",
        "risk_expected",
        "risk_actual",
        "risk_match",
        "structured_failure_kind",
        "run_failure_kind",
        "evaluation_passed",
        "primary_failure_class",
        "error_code",
        "diagnostic",
        "plan_schema_valid",
        "unknown_tool_count",
        "output_leak_detected",
        "chat_contract_failures",
        "response_only_contract_verified",
        "benchmark_capabilities",
        "policy_denial_evidence",
        "memory_fixture_evidence_required",
        "duration_seconds",
    }
)
OPTIONAL_TASK_FIELDS = frozenset(
    {
        "benchmark",
        "chat_agent",
        "chat_delegated",
        "memory_lifecycle_evidence",
        "memory_fixture_evidence",
    }
)
SUMMARY_COUNT_FIELDS = (
    "tasks_total",
    "tasks_ran",
    "benchmark_tasks_ran",
    "adversarial_cases_ran",
    "tasks_errored",
    "infrastructure_failure_count",
    "evaluation_failure_count",
    "adversarial_cases_failed",
)
COUNT_FIELDS = (*SUMMARY_COUNT_FIELDS, "golden_tasks_ran")
ZERO_FAILURE_FIELDS = (
    "tasks_errored",
    "infrastructure_failure_count",
    "evaluation_failure_count",
    "adversarial_cases_failed",
)


@dataclass(frozen=True)
class RunBinding:
    repository: str
    commit: str
    run_id: str
    run_attempt: str
    build_identifier: str


@dataclass(frozen=True)
class CorpusContract:
    tasks: tuple[dict[str, Any], ...]
    dataset: dict[str, Any]
    golden_dataset_sha256: str
    benchmark_catalog_sha256: str


@dataclass(frozen=True)
class ReportContract:
    report_sha256: str
    golden_dataset_sha256: str
    benchmark_catalog_sha256: str
    counts: dict[str, int]
