"""真实 LLM 评测轨道 B（real-LLM golden replay harness）。

与 MockProvider 契约轨道（``npm run golden:gate``）互补：本脚本用**当前真实
配置的 LLM Provider**（云端 OpenAI-compatible 或本地 Ollama / LM Studio /
llama.cpp）重放 ``test_data/golden_tasks/golden_tasks.json`` 中 LLM 相关的
任务（entry 为 runs / chat），并按结果质量口径度量而非硬断言：

- intent_accuracy：plan 工具序列与期望完全一致的比例
- tool_overlap_rate：期望工具被规划命中的比例（宽松口径）
- param_missing_rate：plan 步骤缺少 registry 必填参数的比例
- structured_failure_rate：结构化输出失败的比例
- plan_schema_valid_rate：plan schema 形状有效的比例
- unknown_tool_rate：plan 中出现未知工具的比例
- task_success_rate：终态 phase 落在期望集合内的比例
- risk_match_rate：global risk 与期望一致的比例

报告写入 ``.tmp/qa-evidence/real-llm-eval/real-llm-eval-report.json``。

证据边界：这是机器测得的真实模型行为证据，可作为
``npm run evidence:result-quality-review`` 真人评审的输入材料，但不能替代
真人结果质量签收（可读性/返工率仍需人工评分）。

用法（需先配置真实 Provider，禁止 mock）::

    python scripts/run_real_llm_eval.py
    python scripts/run_real_llm_eval.py --max-tasks 10 --categories system,file
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

GOLDEN_DATASET_PATH = REPO_ROOT / "test_data" / "golden_tasks" / "golden_tasks.json"
DEFAULT_REPORT_DIR = REPO_ROOT / ".tmp" / "qa-evidence" / "real-llm-eval"
TERMINAL_OR_WAITING = {
    "completed",
    "failed",
    "denied",
    "cancelled",
    "awaiting_approval",
}
LLM_ENTRIES = {"runs", "chat"}
EVIDENCE_BOUNDARY = (
    "Machine-measured real-LLM behavior evidence. Input material for human "
    "result-quality review; NOT a human result-quality sign-off, RC sign-off, "
    "or release approval."
)


def _provider_config_failure_reason(exc: BaseException) -> str:
    message = str(exc).lower()
    if "could not be resolved" in message:
        return "configured base URL hostname could not be resolved"
    if "base url" in message and "required" in message:
        return "configured base URL is required"
    if "loopback" in message or "private" in message or "ssrf" in message:
        return "configured base URL targets loopback/private/link-local/metadata hosts"
    if "absolute http" in message:
        return "configured base URL is not an absolute http(s) URL"
    if "api_key" in message:
        return "cloud provider is missing an API key"
    if "unsupported cloud provider" in message:
        return "configured provider is not supported for cloud routing"
    return f"{type(exc).__name__} while validating provider configuration"


def _provider_config_exit_message(exc: BaseException) -> str:
    return "\n".join(
        [
            "real-llm-eval: real provider preflight failed.",
            f"Reason: {_provider_config_failure_reason(exc)}.",
            (
                "The real LLM quality gate requires a non-mock provider with a "
                "non-private cloud/OpenAI-compatible base URL and API key before "
                "any golden tasks run."
            ),
            (
                "Configure LENGRVIS_PROVIDER_NAME, LENGRVIS_BASE_URL=https://..., "
                "LENGRVIS_API_KEY, LENGRVIS_MODEL, and LENGRVIS_MODE=efficiency "
                "(or matching config.yaml/.env values)."
            ),
            (
                "Loopback, private/LAN, link-local, and metadata hosts are blocked "
                "by the SSRF guard for this gate."
            ),
            "Secrets and configured URL values are intentionally omitted from this diagnostic.",
        ]
    )


def _local_provider_failure_reason(exc: BaseException) -> str:
    message = str(exc).lower()
    if "non-local base_url" in message:
        return "configured local provider base URL is not local"
    if "unable to load onnx" in message:
        return "configured ONNX local model could not be loaded"
    if "onnx text generation failed" in message:
        return "local ONNX provider failed during generation"
    if "privacy mode requires" in message or "local llm backend" in message:
        return "no reachable local LLM backend was detected"
    return f"{type(exc).__name__} while starting the local provider"


def _local_provider_exit_message(exc: BaseException) -> str:
    return "\n".join(
        [
            "real-llm-eval: local provider preflight failed.",
            f"Reason: {_local_provider_failure_reason(exc)}.",
            (
                "The real LLM quality gate can run against a local provider, "
                "but the configured local backend was unavailable or rejected "
                "before any golden tasks ran."
            ),
            (
                "Start Ollama, LM Studio, a llama.cpp-compatible local server, "
                "or configure an ONNX local model."
            ),
            (
                "Configure LENGRVIS_PROVIDER_NAME=ollama/lmstudio/llamacpp/onnx, "
                "LENGRVIS_BASE_URL for the local service when needed, "
                "LENGRVIS_MODEL, and LENGRVIS_MODE=privacy."
            ),
            "Secrets and configured URL values are intentionally omitted from this diagnostic.",
        ]
    )


def _should_report_local_provider_failure(settings: Any) -> bool:
    from app.llm.registry import LOCAL_PROVIDERS

    provider_name = (settings.provider_name or "").lower()
    mode = (settings.mode or "efficiency").lower()
    return mode == "privacy" or provider_name in LOCAL_PROVIDERS


def _validate_real_provider_preflight(settings: Any) -> None:
    from app.core.outbound_url import validate_outbound_http_url
    from app.llm.registry import CLOUD_PROVIDERS

    mode = (settings.mode or "efficiency").lower()
    if mode == "privacy":
        return
    provider_name = (settings.provider_name or "").lower()
    if provider_name not in CLOUD_PROVIDERS:
        return
    base_url = str(settings.base_url or "").strip()
    if not base_url:
        raise ValueError("configured base URL is required for cloud/OpenAI-compatible real LLM eval.")
    validate_outbound_http_url(base_url, allow_private=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay golden tasks against the real configured LLM provider."
    )
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument(
        "--max-tasks", type=int, default=0, help="0 = all eligible tasks"
    )
    parser.add_argument(
        "--categories", default="", help="comma-separated category filter"
    )
    parser.add_argument(
        "--task-ids", default="", help="comma-separated golden task id filter"
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="per-task wall clock budget",
    )
    parser.add_argument(
        "--quality-gate",
        action="store_true",
        help="Fail non-zero when real LLM quality metrics miss release thresholds.",
    )
    parser.add_argument("--min-task-success-rate", type=float, default=0.8)
    parser.add_argument("--min-intent-accuracy", type=float, default=0.7)
    parser.add_argument("--min-tool-overlap-rate", type=float, default=0.8)
    parser.add_argument("--min-risk-match-rate", type=float, default=0.8)
    parser.add_argument(
        "--min-task-count",
        type=int,
        default=20,
        help="Minimum real-LLM tasks that must run when --quality-gate is enabled.",
    )
    parser.add_argument("--min-task-success-count", type=int, default=18)
    parser.add_argument("--min-intent-accuracy-count", type=int, default=14)
    parser.add_argument("--min-tool-overlap-count", type=int, default=14)
    parser.add_argument("--min-risk-match-count", type=int, default=9)
    parser.add_argument("--min-param-missing-count", type=int, default=14)
    parser.add_argument("--min-structured-failure-count", type=int, default=20)
    parser.add_argument("--min-unknown-tool-count", type=int, default=14)
    parser.add_argument("--max-param-missing-rate", type=float, default=0.05)
    parser.add_argument("--max-structured-failure-rate", type=float, default=0.0)
    parser.add_argument("--max-unknown-tool-rate", type=float, default=0.0)
    return parser.parse_args()


def _require_real_provider() -> dict[str, str]:
    from app.llm.local_provider import LocalBackendUnavailable
    from app.llm.mock_provider import MockProvider
    from app.llm.registry import get_effective_settings, get_provider_for_mode

    settings = get_effective_settings()
    if (settings.provider_name or "").lower() == "mock":
        raise SystemExit(
            "real-llm-eval refuses to run with provider_name=mock; configure a real provider first."
        )
    try:
        _validate_real_provider_preflight(settings)
    except ValueError as exc:
        raise SystemExit(_provider_config_exit_message(exc)) from None
    try:
        provider = get_provider_for_mode(settings, task="planner")
    except LocalBackendUnavailable as exc:
        if _should_report_local_provider_failure(settings):
            raise SystemExit(_local_provider_exit_message(exc)) from None
        raise SystemExit(_provider_config_exit_message(exc)) from None
    if isinstance(provider, MockProvider):
        raise SystemExit(
            "real-llm-eval resolved MockProvider; configure LENGRVIS_API_KEY / a local backend first."
        )
    return {
        "provider_name": settings.provider_name,
        "model": settings.model,
        "mode": settings.mode,
        "wire_api": getattr(settings, "wire_api", ""),
    }


def _golden_app():
    from fastapi import FastAPI

    from app.api.routes_approvals import router as approvals_router
    from app.api.routes_chat import router as chat_router
    from app.api.routes_files import router as files_router
    from app.api.routes_runs import router as runs_router

    app = FastAPI()
    app.include_router(runs_router, prefix="/api")
    app.include_router(approvals_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(files_router, prefix="/api")
    return app


def _sub(value: Any, workspace: Path, outside: Path) -> Any:
    if isinstance(value, str):
        return value.replace("$WS", str(workspace)).replace("$OUTSIDE", str(outside))
    if isinstance(value, dict):
        return {key: _sub(item, workspace, outside) for key, item in value.items()}
    if isinstance(value, list):
        return [_sub(item, workspace, outside) for item in value]
    return value


class _EnvScope:
    """Set process env vars for one task and restore them afterwards."""

    def __init__(self, values: dict[str, str | None]):
        self._values = values
        self._saved: dict[str, str | None] = {}

    def __enter__(self) -> "_EnvScope":
        for key, value in self._values.items():
            self._saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc_info: Any) -> None:
        for key, original in self._saved.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


def _plan_record(task_id: str) -> dict[str, Any] | None:
    from app.core import db

    plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
    return plans[0] if plans else None


def _required_args_missing(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.tools.registry import registry

    missing: list[dict[str, Any]] = []
    for step in steps:
        tool_name = step.get("tool_name") or ""
        try:
            definition = registry.get(tool_name)
        except Exception:  # noqa: BLE001 - unknown tool is itself a planning miss.
            missing.append({"tool": tool_name, "missing": ["<unknown tool>"]})
            continue
        schema = getattr(definition, "input_schema", None) or {}
        required = schema.get("required") or []
        args = step.get("args") or {}
        absent = [key for key in required if key not in args]
        if absent:
            missing.append({"tool": tool_name, "missing": absent})
    return missing


def _evaluate_task(task: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from app.core import db

    expect = task.get("expect") or {}
    record: dict[str, Any] = {
        "id": task["id"],
        "category": task.get("category", ""),
        "entry": task["entry"],
        "title": task.get("title", ""),
        "ran": False,
        "error": "",
        "phase": "",
        "phase_ok": None,
        "expected_plan_tools": expect.get("plan_tools") or [],
        "actual_plan_tools": [],
        "intent_exact_match": None,
        "expected_tools_planned": None,
        "param_missing": [],
        "risk_expected": expect.get("global_risk", ""),
        "risk_actual": "",
        "risk_match": None,
        "structured_failure_kind": "",
        "plan_schema_valid": None,
        "unknown_tool_count": 0,
        "duration_seconds": 0.0,
    }

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="real-llm-eval-") as tmp:
        tmp_path = Path(tmp)
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside-secret.txt"
        outside.write_text("outside the authorized scope", encoding="utf-8")
        for rel, content in (task.get("fixtures") or {}).items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        env: dict[str, str | None] = {
            "LENGRVIS_DATA_DIR": str(tmp_path / "data"),
            "LENGRVIS_TASK_RECORDING_ENABLED": "false",
            "LENGRVIS_ALLOWED_DIRECTORIES": None
            if task.get("no_scope")
            else str(workspace),
        }
        if task.get("mode"):
            env["LENGRVIS_MODE"] = str(task["mode"])

        try:
            with _EnvScope(env):
                db.init_db()
                message = _sub(task["message"], workspace, outside)
                with TestClient(_golden_app()) as client:
                    if task["entry"] == "runs":
                        record.update(
                            _run_runs_entry(
                                client, task, message, expect, timeout_seconds
                            )
                        )
                    else:
                        record.update(
                            _run_chat_entry(
                                client, task, message, expect, timeout_seconds
                            )
                        )
            record["ran"] = True
        except Exception as exc:  # noqa: BLE001 - single-task failure must not kill the eval.
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["structured_failure_kind"] = str(getattr(exc, "failure_kind", "") or "")
    record["duration_seconds"] = round(time.monotonic() - started, 2)
    return record


def _run_runs_entry(
    client: Any,
    task: dict[str, Any],
    message: str,
    expect: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    created = client.post(
        "/api/runs",
        json={
            "message": message,
            "mode": task.get("mode", "efficiency"),
            "engine": task.get("engine", "os"),
        },
    )
    if created.status_code != 200:
        return {"error": f"run submit failed: HTTP {created.status_code}"}
    run = created.json()
    final = _wait_for_phase(
        client, run["run_id"], set(expect.get("phase") or []), timeout_seconds
    )
    return _measure(final.get("task_id") or "", final.get("phase") or "", expect)


def _run_chat_entry(
    client: Any,
    task: dict[str, Any],
    message: str,
    expect: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    from app.core import db

    response = client.post(
        "/api/chat", json={"message": message, "mode": task.get("mode", "efficiency")}
    )
    if response.status_code != 200:
        return {"error": f"chat submit failed: HTTP {response.status_code}"}
    payload = response.json()
    task_id = payload.get("task_id") or ""
    phase = "completed" if not task_id else ""
    if task_id:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            stored = db.fetch_one("tasks", task_id)
            if stored and stored["status"] in {
                "completed",
                "failed",
                "denied",
                "cancelled",
            }:
                phase = stored["status"]
                break
            time.sleep(0.1)
    expected_phases = expect.get("phase") or (
        ["completed"] if expect.get("task_completed") else []
    )
    measured = _measure(task_id, phase, {**expect, "phase": expected_phases})
    measured.setdefault("chat_delegated", payload.get("delegated"))
    return measured


def _measure(task_id: str, phase: str, expect: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"phase": phase}
    expected_phases = expect.get("phase") or []
    result["phase_ok"] = (phase in expected_phases) if expected_phases else None

    plan = _plan_record(task_id) if task_id else None
    if plan:
        raw_steps = plan.get("steps")
        result["plan_schema_valid"] = isinstance(raw_steps, list) and all(
            isinstance(step, dict) for step in raw_steps
        )
        if not result["plan_schema_valid"]:
            return result
        steps = raw_steps
        actual_tools = [step.get("tool_name") for step in steps]
        result["actual_plan_tools"] = actual_tools
        result["risk_actual"] = plan.get("global_risk_level") or ""
        expected_tools = expect.get("plan_tools") or []
        if expected_tools:
            result["intent_exact_match"] = actual_tools == expected_tools
            result["expected_tools_planned"] = all(
                tool in actual_tools for tool in expected_tools
            )
        if expect.get("global_risk"):
            result["risk_match"] = result["risk_actual"] == expect["global_risk"]
        result["param_missing"] = _required_args_missing(steps)
        result["unknown_tool_count"] = sum(
            1 for item in result["param_missing"] if item.get("missing") == ["<unknown tool>"]
        )
    return result


def _wait_for_phase(
    client: Any, run_id: str, target_phases: set[str], timeout_seconds: float
) -> dict[str, Any]:
    stop_phases = target_phases | TERMINAL_OR_WAITING
    payload: dict[str, Any] = {}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        if response.status_code != 200:
            break
        payload = response.json()
        if payload.get("phase") in stop_phases:
            return payload
        time.sleep(0.1)
    payload.setdefault("phase", "timeout")
    return payload


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    ran = [r for r in records if r["ran"] and not r["error"]]
    phase_known = [r for r in ran if r["phase_ok"] is not None]
    intent_known = [r for r in ran if r["intent_exact_match"] is not None]
    overlap_known = [r for r in ran if r["expected_tools_planned"] is not None]
    risk_known = [r for r in ran if r["risk_match"] is not None]
    planned = [r for r in ran if r["actual_plan_tools"]]
    attempted = [r for r in records if r.get("ran") or r.get("error")]
    plan_schema_known = [r for r in attempted if r.get("plan_schema_valid") is not None]
    task_success_count = sum(1 for r in phase_known if r["phase_ok"])
    intent_accuracy_count = sum(1 for r in intent_known if r["intent_exact_match"])
    tool_overlap_count = sum(1 for r in overlap_known if r["expected_tools_planned"])
    risk_match_count = sum(1 for r in risk_known if r["risk_match"])
    param_missing_count = sum(1 for r in planned if r["param_missing"])
    structured_failure_count = sum(1 for r in attempted if r.get("structured_failure_kind"))
    plan_schema_valid_count = sum(1 for r in plan_schema_known if r.get("plan_schema_valid"))
    unknown_tool_count = sum(1 for r in planned if int(r.get("unknown_tool_count") or 0) > 0)
    return {
        "tasks_total": len(records),
        "tasks_ran": len(ran),
        "tasks_errored": len([r for r in records if r["error"]]),
        "task_success_count": task_success_count,
        "task_success_denominator": len(phase_known),
        "task_success_rate": _rate(task_success_count, len(phase_known)),
        "intent_accuracy_count": intent_accuracy_count,
        "intent_accuracy_denominator": len(intent_known),
        "intent_accuracy": _rate(intent_accuracy_count, len(intent_known)),
        "tool_overlap_count": tool_overlap_count,
        "tool_overlap_denominator": len(overlap_known),
        "tool_overlap_rate": _rate(tool_overlap_count, len(overlap_known)),
        "risk_match_count": risk_match_count,
        "risk_match_denominator": len(risk_known),
        "risk_match_rate": _rate(risk_match_count, len(risk_known)),
        "param_missing_count": param_missing_count,
        "param_missing_denominator": len(planned),
        "param_missing_rate": _rate(param_missing_count, len(planned)),
        "structured_failure_count": structured_failure_count,
        "structured_failure_denominator": len(attempted),
        "structured_failure_rate": _rate(structured_failure_count, len(attempted)),
        "plan_schema_valid_count": plan_schema_valid_count,
        "plan_schema_valid_denominator": len(plan_schema_known),
        "plan_schema_valid_rate": _rate(plan_schema_valid_count, len(plan_schema_known)),
        "unknown_tool_count": unknown_tool_count,
        "unknown_tool_denominator": len(planned),
        "unknown_tool_rate": _rate(unknown_tool_count, len(planned)),
    }


def _quality_gate(summary: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    enabled = bool(args.quality_gate)
    min_task_count = getattr(args, "min_task_count", 0)
    max_structured_failure = getattr(args, "max_structured_failure_rate", 0.0)
    max_unknown_tool = getattr(args, "max_unknown_tool_rate", 0.0)
    thresholds = {
        "min_task_success_rate": args.min_task_success_rate,
        "min_intent_accuracy": args.min_intent_accuracy,
        "min_tool_overlap_rate": args.min_tool_overlap_rate,
        "min_risk_match_rate": args.min_risk_match_rate,
        "min_task_count": min_task_count,
        "min_task_success_count": getattr(args, "min_task_success_count", 0),
        "min_intent_accuracy_count": getattr(args, "min_intent_accuracy_count", 0),
        "min_tool_overlap_count": getattr(args, "min_tool_overlap_count", 0),
        "min_risk_match_count": getattr(args, "min_risk_match_count", 0),
        "min_param_missing_count": getattr(args, "min_param_missing_count", 0),
        "min_structured_failure_count": getattr(args, "min_structured_failure_count", 0),
        "min_unknown_tool_count": getattr(args, "min_unknown_tool_count", 0),
        "max_param_missing_rate": args.max_param_missing_rate,
        "max_structured_failure_rate": max_structured_failure,
        "max_unknown_tool_rate": max_unknown_tool,
    }
    if not enabled:
        return {
            "enabled": False,
            "passed": None,
            "thresholds": thresholds,
            "failures": [],
        }
    failures: list[str] = []
    if summary["tasks_ran"] == 0:
        failures.append("no real-LLM tasks ran")
    if summary["tasks_ran"] < min_task_count:
        failures.append(
            f"tasks_ran={summary['tasks_ran']} below release threshold {min_task_count}"
        )
    for label, denominator_key, minimum in (
        ("task_success_rate", "task_success_denominator", thresholds["min_task_success_count"]),
        ("intent_accuracy", "intent_accuracy_denominator", thresholds["min_intent_accuracy_count"]),
        ("tool_overlap_rate", "tool_overlap_denominator", thresholds["min_tool_overlap_count"]),
        ("risk_match_rate", "risk_match_denominator", thresholds["min_risk_match_count"]),
        ("param_missing_rate", "param_missing_denominator", thresholds["min_param_missing_count"]),
        (
            "structured_failure_rate",
            "structured_failure_denominator",
            thresholds["min_structured_failure_count"],
        ),
        ("unknown_tool_rate", "unknown_tool_denominator", thresholds["min_unknown_tool_count"]),
    ):
        denominator = int(summary.get(denominator_key) or 0)
        if denominator < minimum:
            failures.append(
                f"{label} denominator={denominator} below release threshold {minimum}"
            )
    if summary["tasks_errored"]:
        failures.append(f"{summary['tasks_errored']} real-LLM task(s) errored")
    for key, minimum in (
        ("task_success_rate", args.min_task_success_rate),
        ("intent_accuracy", args.min_intent_accuracy),
        ("tool_overlap_rate", args.min_tool_overlap_rate),
        ("risk_match_rate", args.min_risk_match_rate),
    ):
        value = summary.get(key)
        if value is None:
            failures.append(f"{key} was not measured")
        elif float(value) < minimum:
            failures.append(f"{key}={value} below release threshold {minimum}")
    param_missing = summary.get("param_missing_rate")
    if param_missing is None:
        failures.append("param_missing_rate was not measured")
    elif float(param_missing) > args.max_param_missing_rate:
        failures.append(
            f"param_missing_rate={param_missing} above release threshold {args.max_param_missing_rate}"
        )
    structured_failure = summary.get("structured_failure_rate")
    if structured_failure is None:
        failures.append("structured_failure_rate was not measured")
    elif float(structured_failure) > max_structured_failure:
        failures.append(
            f"structured_failure_rate={structured_failure} above release threshold {max_structured_failure}"
        )
    unknown_tool = summary.get("unknown_tool_rate")
    if unknown_tool is None:
        failures.append("unknown_tool_rate was not measured")
    elif float(unknown_tool) > max_unknown_tool:
        failures.append(f"unknown_tool_rate={unknown_tool} above release threshold {max_unknown_tool}")
    return {
        "enabled": True,
        "passed": not failures,
        "thresholds": thresholds,
        "failures": failures,
    }


def main() -> int:
    args = _parse_args()
    provider_info = _require_real_provider()

    dataset = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))
    tasks: list[dict[str, Any]] = [
        t for t in dataset["tasks"] if t.get("entry") in LLM_ENTRIES
    ]
    if args.categories:
        wanted = {item.strip() for item in args.categories.split(",") if item.strip()}
        tasks = [t for t in tasks if t.get("category") in wanted]
    if args.task_ids:
        wanted_ids = {item.strip() for item in args.task_ids.split(",") if item.strip()}
        tasks = [t for t in tasks if t["id"] in wanted_ids]
    if args.max_tasks > 0:
        tasks = tasks[: args.max_tasks]
    if not tasks:
        raise SystemExit("no eligible golden tasks matched the filters.")

    print(
        f"real-llm-eval: provider={provider_info['provider_name']} model={provider_info['model']} tasks={len(tasks)}"
    )
    records: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, start=1):
        print(f"[{index}/{len(tasks)}] {task['id']} ...", flush=True)
        record = _evaluate_task(task, args.timeout_seconds)
        status = (
            "ERROR"
            if record["error"]
            else ("ok" if record["phase_ok"] in {True, None} else "MISS")
        )
        print(
            f"    -> {status} phase={record['phase']} tools={record['actual_plan_tools']} {record['error']}",
            flush=True,
        )
        records.append(record)

    summary = _aggregate(records)
    quality_gate = _quality_gate(summary, args)
    report = {
        "kind": "real-llm-eval-report",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "provider": provider_info,
        "dataset": str(GOLDEN_DATASET_PATH.relative_to(REPO_ROOT)),
        "evidence_boundary": EVIDENCE_BOUNDARY,
        "summary": summary,
        "quality_gate": quality_gate,
        "tasks": records,
    }
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "real-llm-eval-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.quality_gate:
        print(json.dumps({"quality_gate": quality_gate}, ensure_ascii=False, indent=2))
    print(f"report: {report_path}")

    if summary["tasks_ran"] == 0:
        return 2
    if args.quality_gate and not quality_gate["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
