#!/usr/bin/env python3
"""Emit and verify candidate-bound real-LLM quality evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.real_llm_benchmark_catalog import (  # noqa: E402
    materialize_cases,
    validate_catalog,
)
from scripts.real_llm_eval_reporting import _aggregate, _quality_gate  # noqa: E402
from scripts.real_llm_evidence_tasks import (  # noqa: E402
    TaskEvidenceError,
    validate_expected_risk_contract,
    validate_task_record,
)
from scripts.real_llm_release_profile import ensure_safe_directory  # noqa: E402
from scripts.real_llm_evidence_schema import (  # noqa: E402
    BENCHMARK_CATALOG_RELATIVE,
    COMMAND_TIMEOUT_SECONDS,
    COUNT_FIELDS,
    CorpusContract,
    DATASET_FIELDS,
    DEFAULT_EVIDENCE_PATH,
    DEFAULT_REPORT_PATH,
    EVIDENCE_BOUNDARY,
    GOLDEN_DATASET_RELATIVE,
    LLM_ENTRIES,
    MAX_DATASET_BYTES,
    MAX_EVIDENCE_BYTES,
    MAX_REPORT_BYTES,
    MIN_BENCHMARK_TASKS_RAN,
    MIN_GOLDEN_TASKS_RAN,
    MIN_TASKS_RAN,
    PROVIDER_FIELDS,
    RELEASE_QUALITY_PROFILE,
    REPORT_FIELDS,
    REPORT_KIND,
    REPORT_SCHEMA_VERSION,
    ReportContract,
    RunBinding,
    SCHEMA_VERSION,
    SUMMARY_COUNT_FIELDS,
    TOP_LEVEL_FIELDS,
    ZERO_FAILURE_FIELDS,
)

_GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$")
_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceError(ValueError):
    """Raised when real-LLM evidence cannot be trusted."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise EvidenceError(f"non-standard JSON number is not allowed: {value}")


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0) & reparse_attribute
    )


def _read_regular_file(path: Path, *, max_bytes: int, label: str) -> bytes:
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise EvidenceError(f"{label} file not found: {path}") from exc
    except OSError as exc:
        raise EvidenceError(f"unable to inspect {label}: {exc}") from exc
    if _is_reparse_point(file_stat) or not stat.S_ISREG(file_stat.st_mode):
        raise EvidenceError(f"{label} must be a regular non-symlink file")
    if file_stat.st_size > max_bytes:
        raise EvidenceError(f"{label} exceeds {max_bytes} bytes")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceError(f"unable to read {label}: {exc}") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            _is_reparse_point(opened_stat)
            or not stat.S_ISREG(opened_stat.st_mode)
            or not os.path.samestat(file_stat, opened_stat)
        ):
            raise EvidenceError(f"{label} changed before it could be read safely")
        if opened_stat.st_size > max_bytes:
            raise EvidenceError(f"{label} exceeds {max_bytes} bytes")

        content = bytearray()
        while len(content) <= max_bytes:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > max_bytes:
            raise EvidenceError(f"{label} exceeds {max_bytes} bytes")

        final_stat = os.fstat(descriptor)
        if (
            final_stat.st_size != opened_stat.st_size
            or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
        ):
            raise EvidenceError(f"{label} changed while it was being read")
        return bytes(content)
    except OSError as exc:
        raise EvidenceError(f"unable to read {label}: {exc}") from exc
    finally:
        os.close(descriptor)


def _load_json_bytes(content: bytes, *, label: str) -> Any:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"{label} must be UTF-8 JSON") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{label} is not valid JSON: {exc}") from exc


def _run_git(command: Sequence[str], *, cwd: Path) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError(f"unable to resolve checked-out commit: {exc}") from exc
    if result.returncode != 0:
        raise EvidenceError("unable to resolve checked-out commit")
    value = result.stdout.strip().lower()
    if _FULL_COMMIT_RE.fullmatch(value) is None:
        raise EvidenceError("checked-out commit must be a full 40-character SHA")
    return value


def _checkout_commit(repo_root: Path) -> str:
    return _run_git(
        ("git", "rev-parse", "--verify", "HEAD^{commit}"),
        cwd=repo_root,
    )


def _required_value(
    *,
    label: str,
    explicit: str | None,
    environment: Mapping[str, str],
    environment_names: Sequence[str],
) -> str:
    selected_name: str | None = None
    selected_value: str | None = None
    for name in environment_names:
        if name in environment:
            selected_name = name
            selected_value = environment[name].strip()
            break
    if explicit is not None:
        explicit_value = explicit.strip()
        if selected_value is not None and explicit_value != selected_value:
            raise EvidenceError(f"{label} does not match {selected_name}")
        selected_value = explicit_value
    if not selected_value:
        names = " or ".join(environment_names)
        raise EvidenceError(f"{label} is required via an argument or {names}")
    return selected_value


def _resolve_binding(
    *,
    environment: Mapping[str, str],
    repository: str | None,
    commit: str | None,
    run_id: str | None,
    run_attempt: str | None,
    build_identifier: str | None,
    verify_candidate: bool,
) -> RunBinding:
    names = {
        "repository": ("GITHUB_REPOSITORY",),
        "commit": ("GITHUB_SHA",),
        "run_id": ("GITHUB_RUN_ID",),
        "run_attempt": ("GITHUB_RUN_ATTEMPT",),
        "build_identifier": ("LENGRVIS_RELEASE_BUILD_IDENTIFIER",),
    }
    if verify_candidate:
        names = {
            "repository": (
                "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY",
                "GITHUB_REPOSITORY",
            ),
            "commit": ("LENGRVIS_RELEASE_CANDIDATE_COMMIT", "GITHUB_SHA"),
            "run_id": ("LENGRVIS_RELEASE_CANDIDATE_RUN_ID", "GITHUB_RUN_ID"),
            "run_attempt": (
                "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT",
                "GITHUB_RUN_ATTEMPT",
            ),
            "build_identifier": ("LENGRVIS_RELEASE_BUILD_IDENTIFIER",),
        }
    binding = RunBinding(
        repository=_required_value(
            label="repository",
            explicit=repository,
            environment=environment,
            environment_names=names["repository"],
        ),
        commit=_required_value(
            label="commit",
            explicit=commit,
            environment=environment,
            environment_names=names["commit"],
        ),
        run_id=_required_value(
            label="GitHub run id",
            explicit=run_id,
            environment=environment,
            environment_names=names["run_id"],
        ),
        run_attempt=_required_value(
            label="GitHub run attempt",
            explicit=run_attempt,
            environment=environment,
            environment_names=names["run_attempt"],
        ),
        build_identifier=_required_value(
            label="release build identifier",
            explicit=build_identifier,
            environment=environment,
            environment_names=names["build_identifier"],
        ),
    )
    if _GITHUB_REPOSITORY_RE.fullmatch(binding.repository) is None:
        raise EvidenceError("repository must use the GitHub owner/name format")
    if _FULL_COMMIT_RE.fullmatch(binding.commit) is None:
        raise EvidenceError("commit must be a lowercase full 40-character SHA")
    if _POSITIVE_INTEGER_RE.fullmatch(binding.run_id) is None:
        raise EvidenceError("GitHub run id must be a positive integer")
    if _POSITIVE_INTEGER_RE.fullmatch(binding.run_attempt) is None:
        raise EvidenceError("GitHub run attempt must be a positive integer")
    expected_build_identifier = (
        f"rc-{binding.run_id}-{binding.run_attempt}-{binding.commit}"
    )
    if not hmac.compare_digest(binding.build_identifier, expected_build_identifier):
        raise EvidenceError(
            "release build identifier does not match the candidate run and commit"
        )
    return binding


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _expected_corpus(repo_root: Path) -> CorpusContract:
    golden_bytes = _read_regular_file(
        repo_root / GOLDEN_DATASET_RELATIVE,
        max_bytes=MAX_DATASET_BYTES,
        label="golden dataset",
    )
    catalog_bytes = _read_regular_file(
        repo_root / BENCHMARK_CATALOG_RELATIVE,
        max_bytes=MAX_DATASET_BYTES,
        label="real-LLM benchmark catalog",
    )
    golden = _load_json_bytes(golden_bytes, label="golden dataset")
    catalog = _load_json_bytes(catalog_bytes, label="real-LLM benchmark catalog")
    if not isinstance(golden, dict) or not isinstance(golden.get("tasks"), list):
        raise EvidenceError("golden dataset must contain a tasks array")
    if not isinstance(catalog, dict):
        raise EvidenceError("real-LLM benchmark catalog root must be an object")
    catalog_errors = validate_catalog(catalog)
    if catalog_errors:
        raise EvidenceError(
            "real-LLM benchmark catalog is invalid: " + "; ".join(catalog_errors)
        )
    golden_tasks = [
        task
        for task in golden["tasks"]
        if isinstance(task, dict) and task.get("entry") in LLM_ENTRIES
    ]
    try:
        benchmark_tasks = materialize_cases(catalog)
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError("unable to materialize real-LLM benchmark corpus") from exc
    if len(golden_tasks) < MIN_GOLDEN_TASKS_RAN:
        raise EvidenceError(
            "real-LLM corpus must contain at least "
            f"{MIN_GOLDEN_TASKS_RAN} eligible golden tasks"
        )
    if len(benchmark_tasks) < MIN_BENCHMARK_TASKS_RAN:
        raise EvidenceError(
            "real-LLM corpus must contain at least "
            f"{MIN_BENCHMARK_TASKS_RAN} benchmark tasks"
        )
    tasks = [*golden_tasks, *benchmark_tasks]
    try:
        for task in tasks:
            validate_expected_risk_contract(task)
    except TaskEvidenceError as exc:
        raise EvidenceError(str(exc)) from exc
    task_ids = [str(task.get("id") or "") for task in tasks]
    if any(not task_id for task_id in task_ids) or len(task_ids) != len(set(task_ids)):
        raise EvidenceError("real-LLM corpus task ids must be non-empty and unique")
    golden_sha = _sha256(golden_bytes)
    catalog_sha = _sha256(catalog_bytes)
    dataset = {
        "golden_dataset": GOLDEN_DATASET_RELATIVE.as_posix(),
        "golden_dataset_sha256": golden_sha,
        "benchmark_catalog": BENCHMARK_CATALOG_RELATIVE.as_posix(),
        "benchmark_catalog_sha256": catalog_sha,
        "benchmark_schema_version": catalog["schema_version"],
        "benchmark_evidence_scope": catalog.get("evidence_scope", ""),
        "benchmark_evidence_limitations": catalog.get("evidence_limitations", ""),
        "benchmark_base_scenario_count": len(catalog.get("scenarios") or []),
        "benchmark_variant_count": len(catalog.get("variants") or []),
        "golden_task_count": len(golden_tasks),
        "benchmark_task_count": len(benchmark_tasks),
    }
    return CorpusContract(tuple(tasks), dataset, golden_sha, catalog_sha)


def _fixed_quality_gate_args() -> Namespace:
    gate_profile = {
        key: value
        for key, value in RELEASE_QUALITY_PROFILE.items()
        if key not in {"max_tasks", "categories", "task_ids", "timeout_seconds"}
    }
    return Namespace(quality_gate=True, **gate_profile)


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise EvidenceError("generated_at_utc must be a non-empty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("generated_at_utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise EvidenceError("generated_at_utc must include the UTC offset")


def _validate_provider(provider: Any) -> None:
    if not isinstance(provider, dict) or set(provider) != PROVIDER_FIELDS:
        raise EvidenceError("provider fields must exactly match the v2 report schema")
    for field in ("provider_name", "model", "mode", "wire_api"):
        if not isinstance(provider[field], str):
            raise EvidenceError(f"provider.{field} must be a string")
    if (
        not provider["provider_name"].strip()
        or provider["provider_name"].strip().casefold() == "mock"
        or provider["provider_name"] != provider["provider_name"].strip()
    ):
        raise EvidenceError("provider_name must identify a non-mock provider")
    for field in ("model", "mode"):
        value = provider[field]
        if not value.strip() or value != value.strip():
            raise EvidenceError(f"provider.{field} must be non-empty normalized text")
    if provider["wire_api"] != provider["wire_api"].strip():
        raise EvidenceError("provider.wire_api must be normalized text")
    mode_fields = ("evaluated_modes", "probed_local_modes", "probed_cloud_modes")
    for field in mode_fields:
        values = provider[field]
        if (
            not isinstance(values, list)
            or any(
                not isinstance(value, str) or not value or value != value.strip()
                for value in values
            )
            or values != sorted(set(values))
        ):
            raise EvidenceError(
                f"provider.{field} must be a sorted unique string array"
            )
    evaluated = set(provider["evaluated_modes"])
    local = set(provider["probed_local_modes"])
    cloud = set(provider["probed_cloud_modes"])
    if not evaluated or local.intersection(cloud) or local.union(cloud) != evaluated:
        raise EvidenceError(
            "provider probe modes must exactly cover every evaluated mode"
        )


def _validate_task_record(
    record: Any,
    expected: dict[str, Any],
    *,
    default_mode: str,
) -> None:
    try:
        validate_task_record(record, expected, default_mode=default_mode)
    except TaskEvidenceError as exc:
        raise EvidenceError(str(exc)) from exc


def _report_contract(report_path: Path, *, repo_root: Path) -> ReportContract:
    report_bytes = _read_regular_file(
        report_path,
        max_bytes=MAX_REPORT_BYTES,
        label="real-LLM report",
    )
    report = _load_json_bytes(report_bytes, label="real-LLM report")
    if not isinstance(report, dict) or set(report) != REPORT_FIELDS:
        raise EvidenceError("real-LLM report fields must exactly match the v2 schema")
    if report["schema_version"] != REPORT_SCHEMA_VERSION:
        raise EvidenceError(f"report schema_version must be {REPORT_SCHEMA_VERSION}")
    if report["kind"] != REPORT_KIND:
        raise EvidenceError(f"report kind must be {REPORT_KIND}")
    if report["evidence_boundary"] != EVIDENCE_BOUNDARY:
        raise EvidenceError("real-LLM report evidence boundary is invalid")
    _validate_timestamp(report["generated_at_utc"])
    provider = report["provider"]
    _validate_provider(provider)

    corpus = _expected_corpus(repo_root)
    if (
        not isinstance(report["dataset"], dict)
        or set(report["dataset"]) != DATASET_FIELDS
    ):
        raise EvidenceError("dataset fields must exactly match the v2 report schema")
    if report["dataset"] != corpus.dataset:
        raise EvidenceError("report dataset does not match the checked-out corpus")
    tasks = report["tasks"]
    if not isinstance(tasks, list) or len(tasks) != len(corpus.tasks):
        raise EvidenceError(
            "report must contain the complete unfiltered real-LLM corpus"
        )
    for record, expected in zip(tasks, corpus.tasks, strict=True):
        _validate_task_record(record, expected, default_mode=provider["mode"])
    observed_modes = {record["mode"] for record in tasks}
    if observed_modes != set(provider["evaluated_modes"]):
        raise EvidenceError(
            "provider evaluated_modes must exactly match the task execution modes"
        )

    try:
        normalized_tasks = copy.deepcopy(tasks)
        recomputed_summary = _aggregate(normalized_tasks)
        recomputed_quality_gate = _quality_gate(
            recomputed_summary,
            _fixed_quality_gate_args(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError("unable to recompute real-LLM report metrics") from exc
    if normalized_tasks != tasks:
        raise EvidenceError(
            "task failure attribution does not match recomputed report evidence"
        )
    if report["summary"] != recomputed_summary:
        raise EvidenceError("report summary does not match recomputed task metrics")
    if report["quality_gate"] != recomputed_quality_gate:
        raise EvidenceError(
            "report quality_gate does not match fixed release thresholds"
        )
    if recomputed_quality_gate.get("passed") is not True:
        raise EvidenceError("real-LLM quality gate did not pass")

    counts: dict[str, int] = {}
    for field in SUMMARY_COUNT_FIELDS:
        value = recomputed_summary.get(field)
        if type(value) is not int or value < 0:
            raise EvidenceError(f"summary.{field} must be a non-negative integer")
        counts[field] = value
    expected_golden_count = sum(
        1 for task in corpus.tasks if not isinstance(task.get("benchmark"), dict)
    )
    counts["golden_tasks_ran"] = sum(
        1 for task in normalized_tasks if not isinstance(task.get("benchmark"), dict)
    )
    if counts["tasks_ran"] < MIN_TASKS_RAN:
        raise EvidenceError(f"tasks_ran must be at least {MIN_TASKS_RAN}")
    if counts["benchmark_tasks_ran"] < MIN_BENCHMARK_TASKS_RAN:
        raise EvidenceError(
            f"benchmark_tasks_ran must be at least {MIN_BENCHMARK_TASKS_RAN}"
        )
    if counts["golden_tasks_ran"] < MIN_GOLDEN_TASKS_RAN:
        raise EvidenceError(f"golden_tasks_ran must be at least {MIN_GOLDEN_TASKS_RAN}")
    if counts["adversarial_cases_ran"] <= 0:
        raise EvidenceError("adversarial_cases_ran must be positive")
    for field in ZERO_FAILURE_FIELDS:
        if counts[field] != 0:
            raise EvidenceError(f"summary.{field} must be zero")
    expected_benchmark_count = sum(
        1 for task in corpus.tasks if isinstance(task.get("benchmark"), dict)
    )
    if counts["tasks_total"] != len(corpus.tasks) or counts["tasks_ran"] != len(
        corpus.tasks
    ):
        raise EvidenceError("the formal report must run the complete real-LLM corpus")
    if counts["benchmark_tasks_ran"] != expected_benchmark_count:
        raise EvidenceError("the formal report must run every benchmark case")
    if counts["golden_tasks_ran"] != expected_golden_count:
        raise EvidenceError("the formal report must run every eligible golden task")
    return ReportContract(
        report_sha256=_sha256(report_bytes),
        golden_dataset_sha256=corpus.golden_dataset_sha256,
        benchmark_catalog_sha256=corpus.benchmark_catalog_sha256,
        counts=counts,
    )


def _payload(binding: RunBinding, report: ReportContract) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": binding.repository,
        "commit": binding.commit,
        "github_run_id": binding.run_id,
        "github_run_attempt": binding.run_attempt,
        "build_identifier": binding.build_identifier,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "report_sha256": report.report_sha256,
        "golden_dataset_sha256": report.golden_dataset_sha256,
        "benchmark_catalog_sha256": report.benchmark_catalog_sha256,
        "quality_gate_passed": True,
        **report.counts,
    }


def _write_new_evidence(
    path: Path,
    payload: dict[str, Any],
    *,
    trusted_root: Path,
) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("generated evidence exceeds the evidence size limit")
    ensure_safe_directory(path.parent, trusted_root=trusted_root)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    created_temporary = False
    published = False
    try:
        with temporary.open("xb") as evidence_file:
            created_temporary = True
            evidence_file.write(encoded)
            evidence_file.flush()
            os.fsync(evidence_file.fileno())
        temporary_stat = temporary.lstat()
        try:
            os.link(temporary, path)
            published = True
        except FileExistsError as exc:
            raise EvidenceError(f"refusing to overwrite evidence file: {path}") from exc
        except OSError as exc:
            raise EvidenceError(
                f"unable to publish evidence file atomically: {exc}"
            ) from exc
        final_stat = path.lstat()
        if (
            _is_reparse_point(final_stat)
            or not stat.S_ISREG(final_stat.st_mode)
            or not os.path.samestat(temporary_stat, final_stat)
        ):
            raise EvidenceError(
                "published evidence must be the newly written regular file"
            )
        ensure_safe_directory(path.parent, trusted_root=trusted_root)
    except EvidenceError:
        if published:
            try:
                final_stat = path.lstat()
                temporary_stat = temporary.lstat()
                if os.path.samestat(final_stat, temporary_stat):
                    path.unlink()
            except OSError:
                pass
        raise
    except OSError as exc:
        raise EvidenceError(f"unable to write evidence file: {exc}") from exc
    finally:
        if created_temporary:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def emit_evidence(
    output_path: Path,
    *,
    report_path: Path,
    repo_root: Path,
    environment: Mapping[str, str] | None = None,
    repository: str | None = None,
    commit: str | None = None,
    run_id: str | None = None,
    run_attempt: str | None = None,
    build_identifier: str | None = None,
) -> dict[str, Any]:
    binding = _resolve_binding(
        environment=os.environ if environment is None else environment,
        repository=repository,
        commit=commit,
        run_id=run_id,
        run_attempt=run_attempt,
        build_identifier=build_identifier,
        verify_candidate=False,
    )
    if not hmac.compare_digest(_checkout_commit(repo_root), binding.commit):
        raise EvidenceError("GITHUB_SHA does not match the checked-out commit")
    payload = _payload(binding, _report_contract(report_path, repo_root=repo_root))
    _write_new_evidence(output_path, payload, trusted_root=repo_root)
    return payload


def _validate_payload_shape(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["evidence root must be a JSON object"]
    actual_fields = set(payload)
    if actual_fields != TOP_LEVEL_FIELDS:
        missing = sorted(TOP_LEVEL_FIELDS - actual_fields)
        extra = sorted(actual_fields - TOP_LEVEL_FIELDS)
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected fields: {', '.join(extra)}")
        return [
            "evidence fields must exactly match the v2 schema ("
            + "; ".join(details)
            + ")"
        ]
    errors: list[str] = []
    for field in (
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
    ):
        if type(payload[field]) is not str:
            errors.append(f"{field} must be a string")
    if type(payload["quality_gate_passed"]) is not bool:
        errors.append("quality_gate_passed must be a boolean")
    for field in COUNT_FIELDS:
        if type(payload[field]) is not int or payload[field] < 0:
            errors.append(f"{field} must be a non-negative integer")
    if errors:
        return errors
    if payload["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if _GITHUB_REPOSITORY_RE.fullmatch(payload["repository"]) is None:
        errors.append("repository must use the GitHub owner/name format")
    if _FULL_COMMIT_RE.fullmatch(payload["commit"]) is None:
        errors.append("commit must be a lowercase full 40-character SHA")
    if _POSITIVE_INTEGER_RE.fullmatch(payload["github_run_id"]) is None:
        errors.append("github_run_id must be a positive integer string")
    if _POSITIVE_INTEGER_RE.fullmatch(payload["github_run_attempt"]) is None:
        errors.append("github_run_attempt must be a positive integer string")
    expected_build_identifier = (
        f"rc-{payload['github_run_id']}-{payload['github_run_attempt']}-"
        f"{payload['commit']}"
    )
    if payload["build_identifier"] != expected_build_identifier:
        errors.append("build_identifier must match the candidate run and commit")
    if payload["report_schema_version"] != REPORT_SCHEMA_VERSION:
        errors.append(f"report_schema_version must be {REPORT_SCHEMA_VERSION}")
    for field in (
        "report_sha256",
        "golden_dataset_sha256",
        "benchmark_catalog_sha256",
    ):
        if _SHA256_RE.fullmatch(payload[field]) is None:
            errors.append(f"{field} must be a lowercase SHA256 digest")
    if payload["quality_gate_passed"] is not True:
        errors.append("quality_gate_passed must be true")
    for field in ZERO_FAILURE_FIELDS:
        if payload[field] != 0:
            errors.append(f"{field} must be zero")
    return errors


def verify_evidence(
    input_path: Path,
    *,
    report_path: Path,
    repo_root: Path,
    environment: Mapping[str, str] | None = None,
    repository: str | None = None,
    commit: str | None = None,
    run_id: str | None = None,
    run_attempt: str | None = None,
    build_identifier: str | None = None,
    require_checkout_match: bool = False,
) -> list[str]:
    try:
        payload = _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_EVIDENCE_BYTES,
                label="real-LLM evidence",
            ),
            label="real-LLM evidence",
        )
        errors = _validate_payload_shape(payload)
        if errors:
            return errors
        binding = _resolve_binding(
            environment=os.environ if environment is None else environment,
            repository=repository,
            commit=commit,
            run_id=run_id,
            run_attempt=run_attempt,
            build_identifier=build_identifier,
            verify_candidate=True,
        )
        report = _report_contract(report_path, repo_root=repo_root)
        if require_checkout_match and not hmac.compare_digest(
            _checkout_commit(repo_root), binding.commit
        ):
            errors.append("candidate commit does not match the checked-out commit")
        expected_values: tuple[tuple[str, Any], ...] = (
            ("repository", binding.repository),
            ("commit", binding.commit),
            ("github_run_id", binding.run_id),
            ("github_run_attempt", binding.run_attempt),
            ("build_identifier", binding.build_identifier),
            ("report_schema_version", REPORT_SCHEMA_VERSION),
            ("report_sha256", report.report_sha256),
            ("golden_dataset_sha256", report.golden_dataset_sha256),
            ("benchmark_catalog_sha256", report.benchmark_catalog_sha256),
            ("quality_gate_passed", True),
            *((field, report.counts[field]) for field in COUNT_FIELDS),
        )
        for field, expected in expected_values:
            actual = payload[field]
            matches = (
                hmac.compare_digest(actual, expected)
                if isinstance(actual, str) and isinstance(expected, str)
                else actual == expected
            )
            if not matches:
                errors.append(f"{field} does not match the expected candidate report")
        return errors
    except EvidenceError as exc:
        return [str(exc)]


def _add_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository")
    parser.add_argument("--commit")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--build-identifier")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    emit_parser = subparsers.add_parser(
        "emit", help="Emit passed evidence for this GitHub producer job."
    )
    emit_parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    emit_parser.add_argument("--output", type=Path, default=DEFAULT_EVIDENCE_PATH)
    _add_binding_arguments(emit_parser)
    verify_parser = subparsers.add_parser(
        "verify", help="Verify candidate-bound real-LLM evidence and report."
    )
    verify_parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    verify_parser.add_argument("--input", type=Path, default=DEFAULT_EVIDENCE_PATH)
    verify_parser.add_argument("--require-checkout-match", action="store_true")
    _add_binding_arguments(verify_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    if args.command == "emit":
        try:
            payload = emit_evidence(
                args.output,
                report_path=args.report,
                repo_root=repo_root,
                repository=args.repository,
                commit=args.commit,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                build_identifier=args.build_identifier,
            )
        except EvidenceError as exc:
            print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
            return 1
        print(
            json.dumps(
                {"ok": True, "evidence": str(args.output), "binding": payload},
                indent=2,
            )
        )
        return 0
    errors = verify_evidence(
        args.input,
        report_path=args.report,
        repo_root=repo_root,
        repository=args.repository,
        commit=args.commit,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        build_identifier=args.build_identifier,
        require_checkout_match=args.require_checkout_match,
    )
    print(
        json.dumps(
            {"ok": not errors, "evidence": str(args.input), "errors": errors},
            indent=2,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
