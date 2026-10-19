"""Run reproducible Butler contract scenarios and keep native/model evidence separate."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "archive_edit_undo_restart": ["backend/tests/test_butler_execution_integration.py"],
    "clarification_and_interruption": ["backend/tests/test_butler_coordinator.py", "backend/tests/test_butler_coordinator_extra.py"],
    "source_binding_and_documents": ["backend/tests/test_plan_input_binding.py", "backend/tests/test_document_ai.py::test_real_document_derivations_emit_explicit_field_lineage"],
    "durable_waits_and_residency": ["backend/tests/test_butler_store.py", "backend/tests/test_butler_residency.py"],
    "scoped_concurrency_and_revocation": ["backend/tests/test_butler_runtime.py", "backend/tests/test_butler_api.py"],
    "concrete_browser_confirmation": ["backend/tests/test_butler_browser.py", "backend/tests/test_butler_browser_policy.py"],
    "complete_flow_outcomes_and_continuation": ["backend/tests/test_butler_full_flow.py"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / ".tmp/audit-fixes/butler-evaluation.json")
    parser.add_argument("--real-report", type=Path, action="append", default=[])
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="butler-eval-"))
    env = {key: value for key, value in os.environ.items() if not key.startswith("LENGRVIS_")}
    env.update({
        "LENGRVIS_TEST": "1", "LENGRVIS_AUDIT_HMAC_SECRET": "butler-eval-test-only",
        "LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS": "1", "LENGRVIS_DATA_DIR": str(scratch / "data"),
        "LENGRVIS_ENV_FILE": str(scratch / "absent.env"), "LENGRVIS_CONFIG_FILE": str(scratch / "absent.yaml"),
        "LENGRVIS_PROVIDER_NAME": "mock", "PATH": str(Path(sys.executable).parent) + os.pathsep + env["PATH"],
        "PYTHONUTF8": "1",
    })
    xml_path = args.report.with_suffix(".xml")
    log_path = args.report.with_suffix(".log")
    nodes = list(dict.fromkeys(node for group in SCENARIOS.values() for node in group))
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run([sys.executable, "-m", "pytest", *nodes, "-q", f"--basetemp={scratch / 'pytest'}", f"--junitxml={xml_path}"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
    cases = list(ET.parse(xml_path).iter("testcase")) if xml_path.exists() else []
    scenarios = []
    for name, members in SCENARIOS.items():
        module_names = [node.split("::")[0].removesuffix(".py").replace("/", ".") for node in members]
        selected = [case for case in cases if any(case.get("classname", "").endswith(module) or case.get("classname", "").endswith(module.split(".")[-1]) for module in module_names)]
        passed = sum(not any(case.find(tag) is not None for tag in ("failure", "error", "skipped")) for case in selected)
        scenarios.append({"case": name, "environment": "test_environment", "checks": len(selected), "passed": passed, "ok": bool(selected) and passed == len(selected), "contract_pass_rate": passed / len(selected) if selected else 0})
    real_runs = [json.loads(path.read_text(encoding="utf-8")) for path in args.real_report]
    report = {
        "schema": "butler-evaluation/v1", "created_at": datetime.now(UTC).isoformat(),
        "ok": result.returncode == 0 and all(item["ok"] for item in scenarios) and all(item.get("ok") is True for item in real_runs),
        "contract_scenarios": scenarios, "real_runs": real_runs,
        "metric_policy": "Contract pass rates are separate from real task completion. Real-model runs report interventions, recovery, duplicate actions, wrong changes and model usage. null means that a metric was not exercised.",
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "contract_checks": len(cases), "real_reports": len(real_runs), "report": str(args.report)}, ensure_ascii=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
