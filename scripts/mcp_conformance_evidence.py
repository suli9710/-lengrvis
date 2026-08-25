#!/usr/bin/env python3
"""Emit and verify checkout-bound MCP conformance job evidence."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.mcp_conformance_results import (  # noqa: E402
    EXPECTED_MCP_SPEC_VERSION,
    MAX_CHECKS_BYTES,
    RESULTS_SUMMARY_VERSION,
    SCENARIO_CONTRACTS,
    ConformanceResultsError,
    canonical_sha256 as _canonical_sha256,
    summarize_checks,
    validate_results_summary,
)

SCHEMA_VERSION = "mcp-conformance-job-evidence/v2"
DEFAULT_EVIDENCE_PATH = Path(
    ".tmp/qa-evidence/mcp-conformance-job/mcp-conformance-evidence.json"
)
DEFAULT_RESULTS_ROOT = Path(".tmp/qa-evidence/mcp-conformance")
CONFORMANCE_PACKAGE = "@modelcontextprotocol/conformance"
EXPECTED_CONFORMANCE_VERSION = "0.1.16"
REQUIRED_NODE_MAJOR = 24
PASSED_STATUS = "passed"
MAX_EVIDENCE_BYTES = 16 * 1024
MAX_PACKAGE_JSON_BYTES = 1024 * 1024
MAX_PACKAGE_LOCK_BYTES = 16 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 15

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "repository",
        "commit",
        "github_run_id",
        "github_run_attempt",
        "root_package_lock_sha256",
        "conformance_version",
        "mcp_spec_version",
        "node_major",
        "producer_node_version",
        "results_summary",
        "results_summary_sha256",
        "status",
    }
)
_GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$")
_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NODE_VERSION_RE = re.compile(
    r"^v([1-9][0-9]*)\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
)


class EvidenceError(ValueError):
    """Raised when evidence cannot be emitted or verified safely."""


@dataclass(frozen=True)
class RunBinding:
    repository: str
    commit: str
    run_id: str
    run_attempt: str


@dataclass(frozen=True)
class RepositoryContract:
    package_lock_sha256: str
    conformance_version: str
    node_major: int


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


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
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{label} is not valid JSON: {exc}") from exc


def _load_repository_json(path: Path, *, max_bytes: int, label: str) -> dict[str, Any]:
    payload = _load_json_bytes(
        _read_regular_file(path, max_bytes=max_bytes, label=label),
        label=label,
    )
    if not isinstance(payload, dict):
        raise EvidenceError(f"{label} root must be a JSON object")
    return payload


def _run_version_command(command: Sequence[str], *, cwd: Path, label: str) -> str:
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
        raise EvidenceError(f"unable to resolve {label}: {exc}") from exc
    if result.returncode != 0:
        raise EvidenceError(f"unable to resolve {label}")
    value = result.stdout.strip()
    if not value or "\n" in value or "\r" in value:
        raise EvidenceError(f"{label} returned an invalid value")
    return value


def _checkout_commit(repo_root: Path) -> str:
    commit = _run_version_command(
        ("git", "rev-parse", "--verify", "HEAD^{commit}"),
        cwd=repo_root,
        label="checked-out commit",
    ).lower()
    if _FULL_COMMIT_RE.fullmatch(commit) is None:
        raise EvidenceError("checked-out commit must be a full 40-character SHA")
    return commit


def _current_node_major(repo_root: Path) -> int:
    version = _run_version_command(
        ("node", "--version"),
        cwd=repo_root,
        label="Node version",
    )
    match = _NODE_VERSION_RE.fullmatch(version)
    if match is None:
        raise EvidenceError("Node version must use the vMAJOR.MINOR.PATCH format")
    return int(match.group(1))


def _validate_producer_node_version(version: str) -> str:
    match = _NODE_VERSION_RE.fullmatch(version)
    if match is None:
        raise EvidenceError(
            "producer Node version must use the vMAJOR.MINOR.PATCH format"
        )
    if int(match.group(1)) != REQUIRED_NODE_MAJOR:
        raise EvidenceError(f"producer Node major must be {REQUIRED_NODE_MAJOR}")
    return version


def _resolve_producer_node_version(
    *,
    environment: Mapping[str, str],
    explicit: str | None,
) -> str:
    version = _required_value(
        label="producer Node version",
        explicit=explicit,
        environment=environment,
        environment_names=("MCP_CONFORMANCE_PRODUCER_NODE_VERSION",),
    )
    return _validate_producer_node_version(version)


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


def _validate_binding(binding: RunBinding) -> None:
    if _GITHUB_REPOSITORY_RE.fullmatch(binding.repository) is None:
        raise EvidenceError("repository must use the GitHub owner/name format")
    if _FULL_COMMIT_RE.fullmatch(binding.commit) is None:
        raise EvidenceError("commit must be a lowercase full 40-character SHA")
    if _POSITIVE_INTEGER_RE.fullmatch(binding.run_id) is None:
        raise EvidenceError("GitHub run id must be a positive integer")
    if _POSITIVE_INTEGER_RE.fullmatch(binding.run_attempt) is None:
        raise EvidenceError("GitHub run attempt must be a positive integer")


def _resolve_binding(
    *,
    environment: Mapping[str, str],
    repository: str | None,
    commit: str | None,
    run_id: str | None,
    run_attempt: str | None,
    verify_candidate: bool,
) -> RunBinding:
    environment_names = {
        "repository": ("GITHUB_REPOSITORY",),
        "commit": ("GITHUB_SHA",),
        "run_id": ("GITHUB_RUN_ID",),
        "run_attempt": ("GITHUB_RUN_ATTEMPT",),
    }
    if verify_candidate:
        environment_names = {
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
        }
    binding = RunBinding(
        repository=_required_value(
            label="repository",
            explicit=repository,
            environment=environment,
            environment_names=environment_names["repository"],
        ),
        commit=_required_value(
            label="commit",
            explicit=commit,
            environment=environment,
            environment_names=environment_names["commit"],
        ),
        run_id=_required_value(
            label="GitHub run id",
            explicit=run_id,
            environment=environment,
            environment_names=environment_names["run_id"],
        ),
        run_attempt=_required_value(
            label="GitHub run attempt",
            explicit=run_attempt,
            environment=environment,
            environment_names=environment_names["run_attempt"],
        ),
    )
    _validate_binding(binding)
    return binding


def _repository_contract(repo_root: Path) -> RepositoryContract:
    package = _load_repository_json(
        repo_root / "package.json",
        max_bytes=MAX_PACKAGE_JSON_BYTES,
        label="root package manifest",
    )
    lock_path = repo_root / "package-lock.json"
    lock_bytes = _read_regular_file(
        lock_path,
        max_bytes=MAX_PACKAGE_LOCK_BYTES,
        label="root package lock",
    )
    lock = _load_json_bytes(lock_bytes, label="root package lock")
    if not isinstance(lock, dict):
        raise EvidenceError("root package lock root must be a JSON object")

    package_dependencies = package.get("devDependencies")
    package_version = (
        package_dependencies.get(CONFORMANCE_PACKAGE)
        if isinstance(package_dependencies, dict)
        else None
    )
    lock_packages = lock.get("packages")
    lock_root = lock_packages.get("") if isinstance(lock_packages, dict) else None
    lock_dependencies = (
        lock_root.get("devDependencies") if isinstance(lock_root, dict) else None
    )
    lock_declared_version = (
        lock_dependencies.get(CONFORMANCE_PACKAGE)
        if isinstance(lock_dependencies, dict)
        else None
    )
    lock_package = (
        lock_packages.get(f"node_modules/{CONFORMANCE_PACKAGE}")
        if isinstance(lock_packages, dict)
        else None
    )
    lock_resolved_version = (
        lock_package.get("version") if isinstance(lock_package, dict) else None
    )
    for label, version in (
        ("package.json devDependency", package_version),
        ("package-lock.json root devDependency", lock_declared_version),
        ("package-lock.json resolved package", lock_resolved_version),
    ):
        if version != EXPECTED_CONFORMANCE_VERSION:
            raise EvidenceError(
                f"{label} must pin {CONFORMANCE_PACKAGE} exactly to "
                f"{EXPECTED_CONFORMANCE_VERSION}"
            )

    node_major = _current_node_major(repo_root)
    if node_major != REQUIRED_NODE_MAJOR:
        raise EvidenceError(
            f"Node major must be {REQUIRED_NODE_MAJOR}; got {node_major}"
        )
    return RepositoryContract(
        package_lock_sha256=hashlib.sha256(lock_bytes).hexdigest(),
        conformance_version=EXPECTED_CONFORMANCE_VERSION,
        node_major=node_major,
    )


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(file_attributes & reparse_flag)


def _inspect_directory(path: Path, *, label: str) -> None:
    try:
        directory_stat = path.lstat()
    except FileNotFoundError as exc:
        raise EvidenceError(f"{label} directory not found: {path}") from exc
    except OSError as exc:
        raise EvidenceError(f"unable to inspect {label}: {exc}") from exc
    if _is_reparse_point(directory_stat) or not stat.S_ISDIR(directory_stat.st_mode):
        raise EvidenceError(f"{label} must be a non-reparse directory")


def _directory_entries(path: Path, *, label: str) -> list[Path]:
    _inspect_directory(path, label=label)
    try:
        return sorted(path.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise EvidenceError(f"unable to enumerate {label}: {exc}") from exc


def _require_exact_entry_names(
    entries: Sequence[Path],
    *,
    expected: set[str],
    label: str,
) -> None:
    actual = {entry.name for entry in entries}
    if actual == expected and len(entries) == len(expected):
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing: {', '.join(missing)}")
    if extra:
        details.append(f"unexpected: {', '.join(extra)}")
    if len(entries) != len(actual):
        details.append("duplicate names")
    raise EvidenceError(
        f"{label} must contain the exact expected entries ({'; '.join(details)})"
    )


def _collect_results_summary(results_root: Path) -> dict[str, Any]:
    root_entries = _directory_entries(results_root, label="MCP conformance results")
    _require_exact_entry_names(
        root_entries,
        expected={contract.directory_name for contract in SCENARIO_CONTRACTS},
        label="MCP conformance results",
    )

    scenarios: list[dict[str, Any]] = []
    for contract in SCENARIO_CONTRACTS:
        scenario_root = results_root / contract.directory_name
        run_entries = _directory_entries(
            scenario_root,
            label=f"{contract.scenario} result",
        )
        if len(run_entries) != 1:
            raise EvidenceError(
                f"{contract.scenario} result must contain exactly one official run"
            )
        report_root = run_entries[0]
        _inspect_directory(report_root, label=f"{contract.scenario} report")
        report_entries = _directory_entries(
            report_root,
            label=f"{contract.scenario} report",
        )
        _require_exact_entry_names(
            report_entries,
            expected={"checks.json"},
            label=f"{contract.scenario} report",
        )
        checks_content = _read_regular_file(
            report_root / "checks.json",
            max_bytes=MAX_CHECKS_BYTES,
            label=f"{contract.scenario} checks",
        )
        try:
            summary = summarize_checks(
                checks_content,
                contract=contract,
                report_id=report_root.name,
            )
        except ConformanceResultsError as exc:
            raise EvidenceError(str(exc)) from exc
        scenarios.append(summary)
    return {"schema_version": RESULTS_SUMMARY_VERSION, "scenarios": scenarios}


def _payload(
    binding: RunBinding,
    contract: RepositoryContract,
    results_summary: dict[str, Any],
    *,
    producer_node_version: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": binding.repository,
        "commit": binding.commit,
        "github_run_id": binding.run_id,
        "github_run_attempt": binding.run_attempt,
        "root_package_lock_sha256": contract.package_lock_sha256,
        "conformance_version": contract.conformance_version,
        "mcp_spec_version": EXPECTED_MCP_SPEC_VERSION,
        "node_major": contract.node_major,
        "producer_node_version": producer_node_version,
        "results_summary": results_summary,
        "results_summary_sha256": _canonical_sha256(results_summary),
        "status": PASSED_STATUS,
    }


def _write_new_evidence(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("generated evidence exceeds the evidence size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as evidence_file:
            evidence_file.write(encoded)
            evidence_file.flush()
            os.fsync(evidence_file.fileno())
    except FileExistsError as exc:
        raise EvidenceError(f"refusing to overwrite evidence file: {path}") from exc
    except OSError as exc:
        raise EvidenceError(f"unable to write evidence file: {exc}") from exc


def emit_evidence(
    output_path: Path,
    *,
    repo_root: Path,
    results_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    repository: str | None = None,
    commit: str | None = None,
    run_id: str | None = None,
    run_attempt: str | None = None,
    producer_node_version: str | None = None,
) -> dict[str, Any]:
    """Write passed evidence bound to this GitHub run and checkout."""

    source_environment = os.environ if environment is None else environment
    binding = _resolve_binding(
        environment=source_environment,
        repository=repository,
        commit=commit,
        run_id=run_id,
        run_attempt=run_attempt,
        verify_candidate=False,
    )
    checkout_commit = _checkout_commit(repo_root)
    if not hmac.compare_digest(checkout_commit, binding.commit):
        raise EvidenceError("GITHUB_SHA does not match the checked-out commit")
    resolved_results_root = (
        repo_root / DEFAULT_RESULTS_ROOT if results_root is None else results_root
    )
    results_summary = _collect_results_summary(resolved_results_root)
    payload = _payload(
        binding,
        _repository_contract(repo_root),
        results_summary,
        producer_node_version=_resolve_producer_node_version(
            environment=source_environment,
            explicit=producer_node_version,
        ),
    )
    _write_new_evidence(output_path, payload)
    return payload


def _validate_payload_shape(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["evidence root must be a JSON object"]
    actual_fields = set(payload)
    if actual_fields != _TOP_LEVEL_FIELDS:
        missing = sorted(_TOP_LEVEL_FIELDS - actual_fields)
        extra = sorted(actual_fields - _TOP_LEVEL_FIELDS)
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected fields: {', '.join(extra)}")
        return [
            "evidence fields must exactly match the declared schema ("
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
        "root_package_lock_sha256",
        "conformance_version",
        "mcp_spec_version",
        "producer_node_version",
        "results_summary_sha256",
        "status",
    ):
        if type(payload[field]) is not str:
            errors.append(f"{field} must be a string")
    if type(payload["node_major"]) is not int:
        errors.append("node_major must be an integer")
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
    if _SHA256_RE.fullmatch(payload["root_package_lock_sha256"]) is None:
        errors.append("root_package_lock_sha256 must be a lowercase SHA256 digest")
    if payload["conformance_version"] != EXPECTED_CONFORMANCE_VERSION:
        errors.append(
            f"conformance_version must be exactly {EXPECTED_CONFORMANCE_VERSION}"
        )
    if payload["mcp_spec_version"] != EXPECTED_MCP_SPEC_VERSION:
        errors.append(f"mcp_spec_version must be exactly {EXPECTED_MCP_SPEC_VERSION}")
    if payload["node_major"] != REQUIRED_NODE_MAJOR:
        errors.append(f"node_major must be {REQUIRED_NODE_MAJOR}")
    try:
        _validate_producer_node_version(payload["producer_node_version"])
    except EvidenceError as exc:
        errors.append(str(exc))
    errors.extend(validate_results_summary(payload["results_summary"]))
    if _SHA256_RE.fullmatch(payload["results_summary_sha256"]) is None:
        errors.append("results_summary_sha256 must be a lowercase SHA256 digest")
    elif not hmac.compare_digest(
        payload["results_summary_sha256"],
        _canonical_sha256(payload["results_summary"]),
    ):
        errors.append("results_summary_sha256 does not match results_summary")
    if payload["status"] != PASSED_STATUS:
        errors.append(f"status must be {PASSED_STATUS}")
    return errors


def verify_evidence(
    input_path: Path,
    *,
    repo_root: Path,
    environment: Mapping[str, str] | None = None,
    repository: str | None = None,
    commit: str | None = None,
    run_id: str | None = None,
    run_attempt: str | None = None,
    require_checkout_match: bool = False,
) -> list[str]:
    """Return errors unless evidence matches the expected candidate and checkout."""

    try:
        payload = _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_EVIDENCE_BYTES,
                label="MCP conformance evidence",
            ),
            label="MCP conformance evidence",
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
            verify_candidate=True,
        )
        contract = _repository_contract(repo_root)
        if require_checkout_match:
            checkout_commit = _checkout_commit(repo_root)
            if not hmac.compare_digest(checkout_commit, binding.commit):
                errors.append("candidate commit does not match the checked-out commit")

        expected_values: tuple[tuple[str, Any], ...] = (
            ("repository", binding.repository),
            ("commit", binding.commit),
            ("github_run_id", binding.run_id),
            ("github_run_attempt", binding.run_attempt),
            ("root_package_lock_sha256", contract.package_lock_sha256),
            ("conformance_version", contract.conformance_version),
            ("node_major", contract.node_major),
            ("status", PASSED_STATUS),
        )
        for field, expected in expected_values:
            actual = payload[field]
            matches = (
                hmac.compare_digest(actual, expected)
                if isinstance(actual, str) and isinstance(expected, str)
                else actual == expected
            )
            if not matches:
                errors.append(f"{field} does not match the expected candidate")
        return errors
    except EvidenceError as exc:
        return [str(exc)]


def _add_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository")
    parser.add_argument("--commit")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    emit_parser = subparsers.add_parser(
        "emit", help="Emit passed evidence for this GitHub job."
    )
    emit_parser.add_argument("--output", type=Path, default=DEFAULT_EVIDENCE_PATH)
    emit_parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help="Clean root containing exactly one official report for each scenario.",
    )
    emit_parser.add_argument(
        "--producer-node-version",
        help="Full Node version captured by a trusted step before the CLI ran.",
    )
    _add_binding_arguments(emit_parser)

    verify_parser = subparsers.add_parser(
        "verify", help="Verify candidate-bound MCP evidence."
    )
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
                repo_root=repo_root,
                results_root=args.results_root,
                repository=args.repository,
                commit=args.commit,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                producer_node_version=args.producer_node_version,
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
        repo_root=repo_root,
        repository=args.repository,
        commit=args.commit,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
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
    sys.exit(main())
