"""Release-grade contracts for real-LLM reports and sealed evidence."""

from __future__ import annotations

import json
import shutil
from argparse import Namespace
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import real_llm_evidence as evidence
from scripts import real_llm_release_profile as release_profile
from scripts.real_llm_eval_fixtures import benchmark_capabilities
from scripts.real_llm_eval_reporting import _aggregate, _quality_gate
from scripts.real_llm_eval_safety import _requires_memory_lifecycle_evidence
from scripts.real_llm_evidence_schema import (
    COUNT_FIELDS,
    EVIDENCE_BOUNDARY,
    REPORT_KIND,
    REPORT_SCHEMA_VERSION,
    ReportContract,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
ENVIRONMENT = {
    "GITHUB_REPOSITORY": "owner/repository",
    "GITHUB_SHA": COMMIT,
    "GITHUB_RUN_ID": "123456",
    "GITHUB_RUN_ATTEMPT": "2",
    "LENGRVIS_RELEASE_BUILD_IDENTIFIER": f"rc-123456-2-{COMMIT}",
}


def _empty_denial() -> dict[str, object]:
    return {
        "verified": False,
        "verification_error": "",
        "run_denied_event": False,
        "denying_review_count": 0,
        "review_target_types": [],
        "risk_levels": [],
    }


def _verified_denial(risk_level: str = "R4_FORBIDDEN_OR_HANDOFF") -> dict[str, object]:
    return {
        "verified": True,
        "verification_error": "",
        "run_denied_event": True,
        "denying_review_count": 1,
        "review_target_types": ["task"],
        "risk_levels": [risk_level],
    }


def _unchanged_memory() -> dict[str, object]:
    summary = {
        "total": 0,
        "states": {"active": 0, "quarantined": 0, "revoked": 0, "other": 0},
    }
    return {
        "verified": True,
        "verification_error": "",
        "before": summary,
        "after": deepcopy(summary),
        "created_count": 0,
        "removed_count": 0,
        "modified_count": 0,
        "state_transition_count": 0,
        "unexpected_persistence_count": 0,
    }


def _expired_fixture_evidence() -> dict[str, object]:
    return {
        "verified": True,
        "verification_error": "",
        "seeded": True,
        "seed_active": True,
        "seed_user_confirmed": True,
        "seed_expired": True,
        "recall_probe_executed": True,
        "fixture_recalled": False,
        "fixture_present_after": True,
        "use_count_unchanged": True,
        "last_used_at_unchanged": True,
    }


def _response_only_declared(expect: dict[str, object]) -> bool:
    markers = expect.get("reply_contains") or expect.get("reply_excludes")
    return expect.get("delegated") is False and expect.get("no_tasks") is True and bool(markers)


def _passing_record(task: dict[str, object], *, default_mode: str) -> dict[str, object]:
    expect = task.get("expect") or {}
    assert isinstance(expect, dict)
    expected_tools = expect.get("plan_tools") or expect.get("task_plan_tools") or []
    assert isinstance(expected_tools, list)
    risk_expected = str(expect.get("global_risk") or "")
    has_plan = bool(expected_tools)
    phases = expect.get("phase") or (["completed"] if expect.get("task_completed") else [])
    assert isinstance(phases, list)
    benchmark = task.get("benchmark")
    adversarial_without_plan = bool(
        isinstance(benchmark, dict)
        and benchmark.get("attack_vector") not in {"", "benign"}
        and not has_plan
        and "denied" in phases
    )
    phase = "denied" if adversarial_without_plan else str(phases[0] if phases else "completed")
    if has_plan or phase == "denied":
        assert risk_expected
    mode = str(task.get("mode") or default_mode).strip().casefold()
    record: dict[str, object] = {
        "id": task["id"],
        "category": task.get("category", ""),
        "entry": task["entry"],
        "title": task.get("title", ""),
        "mode": mode,
        "ran": True,
        "error": "",
        "phase": phase,
        "phase_ok": phase in phases if phases else None,
        "expected_plan_tools": expected_tools,
        "actual_plan_tools": list(expected_tools) if has_plan else [],
        "intent_exact_match": (True if has_plan and ("plan_tools" in expect or "task_plan_tools" in expect) else None),
        "expected_tools_planned": (
            True if has_plan and ("plan_tools" in expect or "task_plan_tools" in expect) else None
        ),
        "param_missing": [],
        "risk_expected": risk_expected,
        "risk_actual": risk_expected if has_plan else "",
        "risk_match": True if has_plan and risk_expected else None,
        "structured_failure_kind": "",
        "run_failure_kind": "",
        "evaluation_passed": False,
        "primary_failure_class": "",
        "error_code": "",
        "diagnostic": "",
        "plan_schema_valid": True if has_plan else None,
        "unknown_tool_count": 0,
        "output_leak_detected": False,
        "chat_contract_failures": [],
        "response_only_contract_verified": bool(
            task["entry"] == "chat" and _response_only_declared(expect) and not has_plan
        ),
        "benchmark_capabilities": benchmark_capabilities(task),
        "policy_denial_evidence": (_verified_denial(risk_expected) if phase == "denied" else _empty_denial()),
        "memory_fixture_evidence_required": False,
        "duration_seconds": 0.01,
    }
    if task["entry"] == "chat":
        record["chat_delegated"] = bool(expect.get("delegated", False))
        record["chat_agent"] = str(expect.get("agent") or "")
    if isinstance(benchmark, dict):
        record["benchmark"] = {
            key: str(benchmark.get(key) or "")
            for key in (
                "schema_version",
                "scenario_id",
                "variant_id",
                "attack_vector",
                "evidence_kind",
            )
        }
    if _requires_memory_lifecycle_evidence(task):
        record["memory_lifecycle_evidence"] = _unchanged_memory()
    raw_fixture = task.get("memory_fixture")
    if isinstance(raw_fixture, dict) and raw_fixture.get("expired") is True:
        record["memory_fixture_evidence_required"] = True
        record["memory_fixture_evidence"] = _expired_fixture_evidence()
    return record


def _valid_report() -> dict[str, object]:
    corpus = evidence._expected_corpus(REPO_ROOT)
    provider = {
        "provider_name": "openai_compatible",
        "model": "release-test-model",
        "mode": "efficiency",
        "evaluated_modes": ["efficiency"],
        "probed_local_modes": [],
        "probed_cloud_modes": ["efficiency"],
        "wire_api": "responses",
    }
    records = [_passing_record(task, default_mode=provider["mode"]) for task in corpus.tasks]
    summary = _aggregate(records)
    quality_gate = _quality_gate(summary, evidence._fixed_quality_gate_args())
    assert quality_gate["passed"] is True
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "provider": provider,
        "dataset": corpus.dataset,
        "evidence_boundary": EVIDENCE_BOUNDARY,
        "summary": summary,
        "quality_gate": quality_gate,
        "tasks": records,
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def valid_report() -> dict[str, object]:
    return _valid_report()


def test_complete_recomputed_report_is_accepted(
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    report_path = tmp_path / "report.json"
    _write_report(report_path, valid_report)

    contract = evidence._report_contract(report_path, repo_root=REPO_ROOT)

    assert contract.counts["tasks_ran"] == len(valid_report["tasks"])
    assert contract.counts["golden_tasks_ran"] == 25
    assert contract.counts["benchmark_tasks_ran"] >= 100
    assert contract.counts["evaluation_failure_count"] == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda report: report["provider"].__setitem__("provider_name", " Mock "), "non-mock"),
        (
            lambda report: report["tasks"][0].__setitem__("phase_ok", not report["tasks"][0]["phase_ok"]),
            "phase_ok",
        ),
        (
            lambda report: next(task for task in report["tasks"] if task["expected_plan_tools"]).__setitem__(
                "intent_exact_match", False
            ),
            "intent_exact_match",
        ),
        (
            lambda report: next(task for task in report["tasks"] if task["risk_expected"]).__setitem__(
                "risk_match", False
            ),
            "risk_match",
        ),
        (
            lambda report: next(
                task for task in report["tasks"] if task["memory_fixture_evidence_required"]
            ).__setitem__("memory_fixture_evidence_required", False),
            "memory fixture",
        ),
        (
            lambda report: next(
                task for task in report["tasks"] if task["response_only_contract_verified"]
            ).__setitem__("response_only_contract_verified", False),
            "response-only",
        ),
        (
            lambda report: report["tasks"][0].__setitem__("mode", "privacy"),
            "mode",
        ),
        (
            lambda report: report["summary"].__setitem__("tasks_ran", 0),
            "summary",
        ),
    ],
)
def test_report_contract_rejects_semantic_tampering(
    tmp_path: Path,
    valid_report: dict[str, object],
    mutation,
    message: str,
) -> None:
    report = deepcopy(valid_report)
    mutation(report)
    report_path = tmp_path / "report.json"
    _write_report(report_path, report)

    with pytest.raises(evidence.EvidenceError, match=message):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_report_contract_rejects_attribution_and_dataset_tampering(
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    report = deepcopy(valid_report)
    report["tasks"][0]["evaluation_passed"] = False
    report_path = tmp_path / "attribution.json"
    _write_report(report_path, report)
    with pytest.raises(evidence.EvidenceError, match="failure attribution"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)

    report = deepcopy(valid_report)
    report["dataset"]["golden_dataset_sha256"] = "f" * 64
    report_path = tmp_path / "dataset.json"
    _write_report(report_path, report)
    with pytest.raises(evidence.EvidenceError, match="dataset does not match"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_json_loader_rejects_duplicate_keys_and_nonstandard_numbers() -> None:
    with pytest.raises(evidence.EvidenceError, match="duplicate JSON field"):
        evidence._load_json_bytes(b'{"passed":true,"passed":false}', label="test")
    with pytest.raises(evidence.EvidenceError, match="non-standard JSON number"):
        evidence._load_json_bytes(b'{"score":NaN}', label="test")


def _copy_expected_corpus(tmp_path: Path) -> tuple[Path, Path]:
    golden_target = tmp_path / evidence.GOLDEN_DATASET_RELATIVE
    catalog_target = tmp_path / evidence.BENCHMARK_CATALOG_RELATIVE
    for source, target in (
        (REPO_ROOT / evidence.GOLDEN_DATASET_RELATIVE, golden_target),
        (REPO_ROOT / evidence.BENCHMARK_CATALOG_RELATIVE, catalog_target),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return golden_target, catalog_target


def test_expected_corpus_rejects_golden_task_shrinkage(tmp_path: Path) -> None:
    golden_target, _catalog_target = _copy_expected_corpus(tmp_path)
    golden = json.loads(golden_target.read_text(encoding="utf-8"))
    removed = False
    retained = []
    for task in golden["tasks"]:
        if not removed and task.get("entry") in {"runs", "chat"}:
            removed = True
            continue
        retained.append(task)
    golden["tasks"] = retained
    golden_target.write_text(json.dumps(golden), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="at least 25 eligible golden"):
        evidence._expected_corpus(tmp_path)


@pytest.mark.parametrize(
    ("dataset", "collection", "task_id"),
    [
        ("golden", "tasks", "gt-run-del-await"),
        ("golden", "tasks", "gt-run-deny-password"),
        ("benchmark", "scenarios", "memory-web-poison"),
    ],
)
def test_expected_corpus_rejects_missing_plan_or_denial_global_risk(
    tmp_path: Path,
    dataset: str,
    collection: str,
    task_id: str,
) -> None:
    golden_target, catalog_target = _copy_expected_corpus(tmp_path)
    target = golden_target if dataset == "golden" else catalog_target
    document = json.loads(target.read_text(encoding="utf-8"))
    task = next(item for item in document[collection] if item["id"] == task_id)
    task["expect"].pop("global_risk")
    target.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        evidence.EvidenceError,
        match="plan/denied task must declare global_risk",
    ):
        evidence._expected_corpus(tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda record: record["memory_lifecycle_evidence"].__setitem__("unexpected_payload", "secret"),
            "memory_lifecycle_evidence",
        ),
        (
            lambda record: record["memory_lifecycle_evidence"]["before"]["states"].__setitem__("unexpected", 0),
            "memory lifecycle before",
        ),
        (
            lambda record: record["memory_lifecycle_evidence"].__setitem__("modified_count", 1),
            "memory lifecycle must be unchanged",
        ),
    ],
)
def test_report_contract_rejects_ambiguous_memory_lifecycle_evidence(
    tmp_path: Path,
    valid_report: dict[str, object],
    mutation,
    message: str,
) -> None:
    report = deepcopy(valid_report)
    record = next(task for task in report["tasks"] if "memory_lifecycle_evidence" in task)
    mutation(record)
    report_path = tmp_path / "memory-lifecycle.json"
    _write_report(report_path, report)

    with pytest.raises(evidence.EvidenceError, match=message):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_report_contract_rejects_ambiguous_memory_fixture_evidence(
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    report = deepcopy(valid_report)
    record = next(task for task in report["tasks"] if "memory_fixture_evidence" in task)
    record["memory_fixture_evidence"]["unexpected_payload"] = "secret"
    report_path = tmp_path / "memory-fixture.json"
    _write_report(report_path, report)

    with pytest.raises(evidence.EvidenceError, match="memory_fixture_evidence"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_report_contract_binds_policy_denial_to_phase_and_risk(
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    report = deepcopy(valid_report)
    completed = next(task for task in report["tasks"] if task["phase"] != "denied")
    completed["policy_denial_evidence"] = _verified_denial()
    report_path = tmp_path / "completed-denial.json"
    _write_report(report_path, report)
    with pytest.raises(evidence.EvidenceError, match="non-denied task"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)

    report = deepcopy(valid_report)
    denied = next(task for task in report["tasks"] if task["phase"] == "denied")
    denied["policy_denial_evidence"] = _empty_denial()
    report_path = tmp_path / "missing-denial.json"
    _write_report(report_path, report)
    with pytest.raises(evidence.EvidenceError, match="requires verified"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)

    report = deepcopy(valid_report)
    early_denial = next(
        task
        for task in report["tasks"]
        if task["phase"] == "denied"
        and task["risk_expected"] == "R3_DESTRUCTIVE_OR_SYSTEM"
        and not task["actual_plan_tools"]
    )
    early_denial["policy_denial_evidence"]["risk_levels"] = ["R0_READ_ONLY"]
    report_path = tmp_path / "denial-risk.json"
    _write_report(report_path, report)
    with pytest.raises(evidence.EvidenceError, match="actual or expected risk"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_passing_report_models_all_denials_with_bound_risk(
    valid_report: dict[str, object],
) -> None:
    risk_only_denials = [
        task
        for task in valid_report["tasks"]
        if task["phase"] == "denied" and task["risk_expected"] and not task["expected_plan_tools"]
    ]

    assert len(risk_only_denials) == 21
    assert all(task["actual_plan_tools"] == [] for task in risk_only_denials)
    assert all(task["plan_schema_valid"] is None for task in risk_only_denials)
    assert all(task["risk_actual"] == "" for task in risk_only_denials)
    assert all(task["risk_expected"] in task["policy_denial_evidence"]["risk_levels"] for task in risk_only_denials)


@pytest.mark.parametrize("task_id", ["gt-run-del-await", "gt-run-deny-password"])
def test_report_contract_rejects_corpus_risk_tampering(
    tmp_path: Path,
    valid_report: dict[str, object],
    task_id: str,
) -> None:
    report = deepcopy(valid_report)
    record = next(task for task in report["tasks"] if task["id"] == task_id)
    record["risk_expected"] = "R0_READ_ONLY"
    if record["actual_plan_tools"]:
        record["risk_actual"] = "R0_READ_ONLY"
    else:
        record["policy_denial_evidence"]["risk_levels"] = ["R0_READ_ONLY"]
    report_path = tmp_path / f"{task_id}-risk-tamper.json"
    _write_report(report_path, report)

    with pytest.raises(evidence.EvidenceError, match="risk_expected"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_report_contract_v2_schema_diagnostic_rejects_extra_field(
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    report = deepcopy(valid_report)
    report["legacy_v1_field"] = True
    report_path = tmp_path / "legacy-field.json"
    _write_report(report_path, report)

    with pytest.raises(evidence.EvidenceError, match="v2 schema"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda task: task.update(actual_plan_tools=[]),
            "at least one tool step",
        ),
        (
            lambda task: task.update(risk_actual="R9_IMAGINARY"),
            "supported actual risk",
        ),
        (
            lambda task: task.update(
                actual_plan_tools=[],
                plan_schema_valid=False,
                risk_actual="",
            ),
            "valid plan schema",
        ),
    ],
)
def test_report_contract_rejects_invalid_observed_plan_contracts(
    tmp_path: Path,
    valid_report: dict[str, object],
    mutation,
    message: str,
) -> None:
    report = deepcopy(valid_report)
    planned = next(task for task in report["tasks"] if task["actual_plan_tools"])
    mutation(planned)
    report_path = tmp_path / "invalid-plan.json"
    _write_report(report_path, report)

    with pytest.raises(evidence.EvidenceError, match=message):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_report_contract_rejects_actual_risk_without_a_plan(
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    report = deepcopy(valid_report)
    early_denial = next(
        task
        for task in report["tasks"]
        if task["phase"] == "denied" and task["risk_expected"] and not task["actual_plan_tools"]
    )
    early_denial["risk_actual"] = early_denial["risk_expected"]
    report_path = tmp_path / "risk-without-plan.json"
    _write_report(report_path, report)

    with pytest.raises(evidence.EvidenceError, match="without a plan"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_report_contract_rejects_plan_for_response_only_task(
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    report = deepcopy(valid_report)
    response_only = next(task for task in report["tasks"] if task["response_only_contract_verified"])
    response_only.update(
        actual_plan_tools=["system.diagnostics"],
        plan_schema_valid=True,
        risk_actual="R0_READ_ONLY",
        response_only_contract_verified=False,
    )
    report_path = tmp_path / "response-only-plan.json"
    _write_report(report_path, report)

    with pytest.raises(evidence.EvidenceError, match="response-only task"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_report_contract_rejects_non_finite_duration_and_unsorted_denial(
    tmp_path: Path,
    valid_report: dict[str, object],
) -> None:
    report = deepcopy(valid_report)
    report_path = tmp_path / "duration.json"
    _write_report(report_path, report)
    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace(
            '"duration_seconds": 0.01',
            '"duration_seconds": 1e999',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(evidence.EvidenceError, match="finite non-negative"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)

    report = deepcopy(valid_report)
    denied = next(task for task in report["tasks"] if task["phase"] == "denied")
    denied["policy_denial_evidence"]["review_target_types"] = ["task", "goal"]
    report_path = tmp_path / "denial-order.json"
    _write_report(report_path, report)
    with pytest.raises(evidence.EvidenceError, match="sorted unique"):
        evidence._report_contract(report_path, repo_root=REPO_ROOT)


def test_descriptor_reader_rejects_file_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = tmp_path / "expected.json"
    replacement = tmp_path / "replacement.json"
    expected.write_text("{}", encoding="utf-8")
    replacement.write_text('{"tampered":true}', encoding="utf-8")
    real_open = evidence.os.open

    def open_replacement(_path, flags):
        return real_open(replacement, flags)

    monkeypatch.setattr(evidence.os, "open", open_replacement)
    with pytest.raises(evidence.EvidenceError, match="changed before"):
        evidence._read_regular_file(expected, max_bytes=1024, label="test")


def _profile_args(*argv: str) -> Namespace:
    from scripts import run_real_llm_eval as harness

    return harness._parse_args(list(argv))


def test_release_profile_accepts_only_fixed_full_corpus_defaults() -> None:
    args = _profile_args("--quality-gate", "--release-evidence")
    release_profile.validate_release_evidence_profile(
        args,
        default_report_dir=Path(args.report_dir),
    )

    with pytest.raises(SystemExit, match="requires --quality-gate"):
        release_profile.validate_release_evidence_profile(
            _profile_args("--release-evidence"),
            default_report_dir=Path(args.report_dir),
        )

    for field, original in evidence.RELEASE_QUALITY_PROFILE.items():
        changed = deepcopy(args)
        setattr(changed, field, original + 1 if isinstance(original, int | float) else "x")
        with pytest.raises(SystemExit, match="forbids release-profile overrides"):
            release_profile.validate_release_evidence_profile(
                changed,
                default_report_dir=Path(args.report_dir),
            )


def test_formal_report_writer_refuses_overwrite_and_preserves_temp_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "evidence" / "report.json"
    release_profile.write_report(
        output,
        {"passed": True},
        exclusive=True,
        trusted_root=tmp_path,
    )
    original = output.read_bytes()
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        release_profile.write_report(
            output,
            {"passed": False},
            exclusive=True,
            trusted_root=tmp_path,
        )
    assert output.read_bytes() == original

    collision_output = tmp_path / "evidence" / "collision.json"
    monkeypatch.setattr(
        release_profile.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )
    collision = collision_output.with_name(".collision.json.fixed.tmp")
    collision.write_text("owned by another writer", encoding="utf-8")
    with pytest.raises(FileExistsError):
        release_profile.write_report(
            collision_output,
            {"passed": True},
            exclusive=True,
            trusted_root=tmp_path,
        )
    assert collision.read_text(encoding="utf-8") == "owned by another writer"


def _stub_report_contract() -> ReportContract:
    counts = {field: 0 for field in COUNT_FIELDS}
    counts.update(
        tasks_total=130,
        tasks_ran=130,
        golden_tasks_ran=25,
        benchmark_tasks_ran=105,
        adversarial_cases_ran=90,
    )
    return ReportContract(
        report_sha256="1" * 64,
        golden_dataset_sha256="2" * 64,
        benchmark_catalog_sha256="3" * 64,
        counts=counts,
    )


def test_emit_and_verify_bind_candidate_and_refuse_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text("{}", encoding="utf-8")
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(evidence, "_checkout_commit", lambda _root: COMMIT)
    monkeypatch.setattr(
        evidence,
        "_report_contract",
        lambda _path, *, repo_root: _stub_report_contract(),
    )

    payload = evidence.emit_evidence(
        output,
        report_path=report_path,
        repo_root=tmp_path,
        environment=ENVIRONMENT,
    )
    assert payload["commit"] == COMMIT
    assert (
        evidence.verify_evidence(
            output,
            report_path=report_path,
            repo_root=tmp_path,
            environment=ENVIRONMENT,
            require_checkout_match=True,
        )
        == []
    )
    with pytest.raises(evidence.EvidenceError, match="refusing to overwrite"):
        evidence.emit_evidence(
            output,
            report_path=report_path,
            repo_root=tmp_path,
            environment=ENVIRONMENT,
        )

    payload["github_run_attempt"] = "3"
    payload["build_identifier"] = f"rc-123456-3-{COMMIT}"
    output.write_text(json.dumps(payload), encoding="utf-8")
    errors = evidence.verify_evidence(
        output,
        report_path=report_path,
        repo_root=tmp_path,
        environment=ENVIRONMENT,
    )
    assert any("github_run_attempt does not match" in error for error in errors)
