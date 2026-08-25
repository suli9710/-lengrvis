from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "mcp_conformance_evidence.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("mcp_conformance_evidence", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evidence = _load_script()
COMMIT = "a" * 40
REPORT_TIMESTAMP = "2026-08-24T15-00-00-000Z"
ENVIRONMENT = {
    "GITHUB_REPOSITORY": "suli9710/-lengrvis",
    "GITHUB_SHA": COMMIT,
    "GITHUB_RUN_ID": "123456",
    "GITHUB_RUN_ATTEMPT": "2",
    "MCP_CONFORMANCE_PRODUCER_NODE_VERSION": "v24.6.0",
}


def _success_details(check_id: str) -> dict[str, object]:
    if check_id == "mcp-client-initialization":
        return {
            "protocolVersionSent": "2025-11-25",
            "expectedSpecVersion": "2025-11-25",
            "versionMatch": True,
        }
    if check_id == "tool-add-numbers":
        return {"a": 2, "b": 3, "result": 5}
    if check_id == "client-sse-graceful-reconnect":
        return {"getConnectionCount": 1}
    if check_id == "client-sse-retry-timing":
        return {
            "expectedRetryMs": 500,
            "withinTolerance": True,
            "tooEarly": False,
            "veryLate": False,
            "getConnectionCount": 1,
        }
    if check_id == "client-sse-last-event-id":
        return {
            "hasLastEventId": True,
            "lastEventIds": ["event-1"],
            "getConnectionCount": 1,
        }
    raise AssertionError(f"unexpected success check id: {check_id}")


def _scenario_checks(contract) -> list[dict[str, object]]:
    return [
        {
            "id": check_id,
            "name": check_id,
            "status": status_value,
            "details": _success_details(check_id) if status_value == "SUCCESS" else {},
        }
        for status_value, check_id in contract.expected_checks
    ]


def _write_conformance_results(repo_root: Path) -> Path:
    results_root = repo_root / evidence.DEFAULT_RESULTS_ROOT
    for contract in evidence.SCENARIO_CONTRACTS:
        report_root = results_root / contract.directory_name / f"{contract.report_prefix}{REPORT_TIMESTAMP}"
        report_root.mkdir(parents=True)
        (report_root / "checks.json").write_text(
            json.dumps(_scenario_checks(contract)),
            encoding="utf-8",
        )
    return results_root


def _repository(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    dependency = {
        evidence.CONFORMANCE_PACKAGE: evidence.EXPECTED_CONFORMANCE_VERSION,
    }
    (repo_root / "package.json").write_text(
        json.dumps({"devDependencies": dependency}),
        encoding="utf-8",
    )
    (repo_root / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"devDependencies": dependency},
                    f"node_modules/{evidence.CONFORMANCE_PACKAGE}": {
                        "version": evidence.EXPECTED_CONFORMANCE_VERSION,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    _write_conformance_results(repo_root)
    return repo_root


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evidence, "_checkout_commit", lambda _root: COMMIT)
    monkeypatch.setattr(
        evidence,
        "_current_node_major",
        lambda _root: evidence.REQUIRED_NODE_MAJOR,
    )


def _emit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, dict[str, object]]:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    evidence_path = tmp_path / "mcp-conformance-evidence.json"
    payload = evidence.emit_evidence(
        evidence_path,
        repo_root=repo_root,
        environment=ENVIRONMENT,
    )
    return repo_root, evidence_path, payload


def _write_payload(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _report_root(repo_root: Path, scenario: str) -> Path:
    contract = next(item for item in evidence.SCENARIO_CONTRACTS if item.scenario == scenario)
    reports = list((repo_root / evidence.DEFAULT_RESULTS_ROOT / contract.directory_name).iterdir())
    assert len(reports) == 1
    return reports[0]


def test_emit_and_verify_bind_the_current_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root, evidence_path, payload = _emit(tmp_path, monkeypatch)

    assert set(payload) == evidence._TOP_LEVEL_FIELDS
    assert payload["conformance_version"] == evidence.EXPECTED_CONFORMANCE_VERSION
    assert payload["mcp_spec_version"] == evidence.EXPECTED_MCP_SPEC_VERSION
    assert payload["node_major"] == evidence.REQUIRED_NODE_MAJOR
    assert payload["producer_node_version"] == "v24.6.0"
    assert payload["status"] == evidence.PASSED_STATUS
    summary = payload["results_summary"]
    assert isinstance(summary, dict)
    assert [item["scenario"] for item in summary["scenarios"]] == [
        contract.scenario for contract in evidence.SCENARIO_CONTRACTS
    ]
    assert payload["results_summary_sha256"] == evidence._canonical_sha256(summary)
    assert (
        evidence.verify_evidence(
            evidence_path,
            repo_root=repo_root,
            environment=ENVIRONMENT,
            require_checkout_match=True,
        )
        == []
    )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("repository", "someone/else", "repository does not match"),
        ("commit", "b" * 40, "commit does not match"),
        ("github_run_id", "654321", "github_run_id does not match"),
        ("github_run_attempt", "3", "github_run_attempt does not match"),
        (
            "root_package_lock_sha256",
            "f" * 64,
            "root_package_lock_sha256 does not match",
        ),
        (
            "conformance_version",
            "0.1.15",
            "conformance_version must be exactly",
        ),
        ("mcp_spec_version", "2025-03-26", "mcp_spec_version must be exactly"),
        ("producer_node_version", "v22.18.0", "producer Node major must be"),
        ("node_major", 22, "node_major must be"),
        ("status", "failed", "status must be"),
    ],
)
def test_verify_rejects_candidate_or_toolchain_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: object,
    message: str,
) -> None:
    repo_root, evidence_path, original = _emit(tmp_path, monkeypatch)
    payload = deepcopy(original)
    payload[field] = replacement
    _write_payload(evidence_path, payload)

    errors = evidence.verify_evidence(
        evidence_path,
        repo_root=repo_root,
        environment=ENVIRONMENT,
        require_checkout_match=True,
    )

    assert any(message in error for error in errors)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload.update({"unreviewed": True}),
            "unexpected fields: unreviewed",
        ),
        (
            lambda payload: payload.update({"schema_version": "mcp-conformance-job-evidence/v0"}),
            "schema_version must be",
        ),
    ],
)
def test_verify_rejects_extra_fields_and_old_schemas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate,
    message: str,
) -> None:
    repo_root, evidence_path, original = _emit(tmp_path, monkeypatch)
    payload = deepcopy(original)
    mutate(payload)
    _write_payload(evidence_path, payload)

    errors = evidence.verify_evidence(
        evidence_path,
        repo_root=repo_root,
        environment=ENVIRONMENT,
    )

    assert any(message in error for error in errors)


def test_verify_rejects_results_summary_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root, evidence_path, original = _emit(tmp_path, monkeypatch)
    payload = deepcopy(original)
    payload["results_summary"]["scenarios"][0]["checks_sha256"] = "f" * 64
    _write_payload(evidence_path, payload)

    errors = evidence.verify_evidence(
        evidence_path,
        repo_root=repo_root,
        environment=ENVIRONMENT,
    )

    assert "results_summary_sha256 does not match results_summary" in errors


def test_emit_rejects_missing_scenario_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    (_report_root(repo_root, "tools_call") / "checks.json").unlink()

    with pytest.raises(evidence.EvidenceError, match="exact expected entries"):
        evidence.emit_evidence(
            tmp_path / "evidence.json",
            repo_root=repo_root,
            environment=ENVIRONMENT,
        )


def test_emit_rejects_failed_official_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    checks_path = _report_root(repo_root, "initialize") / "checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    checks[0]["status"] = "FAILURE"
    checks_path.write_text(json.dumps(checks), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="is not successful: FAILURE"):
        evidence.emit_evidence(
            tmp_path / "evidence.json",
            repo_root=repo_root,
            environment=ENVIRONMENT,
        )


def test_emit_rejects_nonstandard_json_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    checks_path = _report_root(repo_root, "initialize") / "checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    info_check = next(check for check in checks if check["status"] == "INFO")
    info_check["details"] = {"duration": float("nan")}
    checks_path.write_text(json.dumps(checks), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="non-standard JSON number"):
        evidence.emit_evidence(
            tmp_path / "evidence.json",
            repo_root=repo_root,
            environment=ENVIRONMENT,
        )


def test_emit_rejects_exponent_overflow_in_informational_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    checks_path = _report_root(repo_root, "initialize") / "checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    info_check = next(check for check in checks if check["status"] == "INFO")
    info_check["details"] = {"duration": 0}
    serialized = json.dumps(checks).replace('"duration": 0', '"duration": 1e999', 1)
    checks_path.write_text(serialized, encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="non-finite JSON number"):
        evidence.emit_evidence(
            tmp_path / "evidence.json",
            repo_root=repo_root,
            environment=ENVIRONMENT,
        )


def test_emit_rejects_tampered_success_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    checks_path = _report_root(repo_root, "tools_call") / "checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    success = next(item for item in checks if item["status"] == "SUCCESS")
    success["details"]["result"] = 6
    checks_path.write_text(json.dumps(checks), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="unexpected result result"):
        evidence.emit_evidence(
            tmp_path / "evidence.json",
            repo_root=repo_root,
            environment=ENVIRONMENT,
        )


def test_emit_rejects_replaced_informational_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    checks_path = _report_root(repo_root, "tools_call") / "checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    checks[0]["id"] = "different-informational-check"
    checks_path.write_text(json.dumps(checks), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="ordered checks"):
        evidence.emit_evidence(
            tmp_path / "evidence.json",
            repo_root=repo_root,
            environment=ENVIRONMENT,
        )


def test_emit_records_report_timestamp_without_wall_clock_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    report_root = _report_root(repo_root, "sse-retry")
    old_report_id = "sse-retry-2020-01-01T00-00-00-000Z"
    report_root.rename(report_root.with_name(old_report_id))

    payload = evidence.emit_evidence(
        tmp_path / "evidence.json",
        repo_root=repo_root,
        environment=ENVIRONMENT,
    )

    scenarios = payload["results_summary"]["scenarios"]
    assert scenarios[2]["report_id"] == old_report_id


def test_emit_rejects_multiple_runs_for_one_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    report_root = _report_root(repo_root, "initialize")
    shutil.copytree(
        report_root,
        report_root.with_name("initialize-2026-08-24T14-59-00-000Z"),
    )

    with pytest.raises(evidence.EvidenceError, match="exactly one official run"):
        evidence.emit_evidence(
            tmp_path / "evidence.json",
            repo_root=repo_root,
            environment=ENVIRONMENT,
        )


def test_emit_requires_trusted_producer_node_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    _patch_runtime(monkeypatch)
    environment = {key: value for key, value in ENVIRONMENT.items() if key != "MCP_CONFORMANCE_PRODUCER_NODE_VERSION"}

    with pytest.raises(evidence.EvidenceError, match="producer Node version is required"):
        evidence.emit_evidence(
            tmp_path / "evidence.json",
            repo_root=repo_root,
            environment=environment,
        )


def test_verify_rejects_oversized_evidence_before_json_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repository(tmp_path)
    monkeypatch.setattr(
        evidence,
        "_current_node_major",
        lambda _root: evidence.REQUIRED_NODE_MAJOR,
    )
    evidence_path = tmp_path / "oversized.json"
    evidence_path.write_bytes(b"{" + b" " * evidence.MAX_EVIDENCE_BYTES + b"}")

    errors = evidence.verify_evidence(
        evidence_path,
        repo_root=repo_root,
        environment=ENVIRONMENT,
    )

    assert errors == [f"MCP conformance evidence exceeds {evidence.MAX_EVIDENCE_BYTES} bytes"]


def test_verify_rejects_checkout_lock_drift_after_evidence_was_emitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root, evidence_path, _payload = _emit(tmp_path, monkeypatch)
    lock_path = repo_root / "package-lock.json"
    lock_path.write_text(lock_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    errors = evidence.verify_evidence(
        evidence_path,
        repo_root=repo_root,
        environment=ENVIRONMENT,
    )

    assert errors == ["root_package_lock_sha256 does not match the expected candidate"]


def test_verify_uses_release_candidate_identity_before_current_github_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root, evidence_path, _payload = _emit(tmp_path, monkeypatch)
    verify_environment = {
        **ENVIRONMENT,
        "GITHUB_RUN_ID": "999999",
        "GITHUB_RUN_ATTEMPT": "9",
        "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY": ENVIRONMENT["GITHUB_REPOSITORY"],
        "LENGRVIS_RELEASE_CANDIDATE_COMMIT": ENVIRONMENT["GITHUB_SHA"],
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ID": ENVIRONMENT["GITHUB_RUN_ID"],
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT": ENVIRONMENT["GITHUB_RUN_ATTEMPT"],
    }

    assert (
        evidence.verify_evidence(
            evidence_path,
            repo_root=repo_root,
            environment=verify_environment,
            require_checkout_match=True,
        )
        == []
    )
