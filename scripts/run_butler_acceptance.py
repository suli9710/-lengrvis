"""Opt-in real-model Butler acceptance on disposable files; never touches user data.

The coordinator, run engine, approvals, file tools, verification and undo are real.
Only the tool catalog and daytime review clock are constrained to the fixture.
Credentials come from configured settings or an un-echoed prompt, never reports.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]


async def exercise(workspace: Path, timeout: int) -> dict:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.core import butler_store as store, db
    from app.core.butler_models import AutonomyScope, ButlerMatter, ButlerMessage, MatterStatus
    from app.llm.usage import list_usage_events
    from app.orchestration.os_execution_engine import OSExecutionEngine
    from app.orchestration.task_rollback_workflow import RollbackSource, TaskRollbackRequest, execute_task_rollback
    from app.policy.policy_engine import PolicyEngine
    from app.services import run_service
    from app.services.butler_service import ButlerCoordinator
    from app.tools.registry import ToolRegistry

    source = workspace / "source.txt"
    target = workspace / "verified-copy.txt"
    content = "管家真实验收\n资料编号：" + secrets.token_hex(4) + "\n保留中文和 emoji 😀。\n"
    source.write_text(content, encoding="utf-8", newline="")
    scope = store.save(AutonomyScope(label="验收临时目录", directories=[str(workspace)], allowed_actions=["observe", "file_modify"]))
    original_init = PolicyEngine.__init__

    def fixed_clock(self, *args, **kwargs):
        kwargs["now_provider"] = lambda: datetime(2026, 9, 16, 12, tzinfo=UTC)
        original_init(self, *args, **kwargs)

    def fixture_orchestrator(_engine):
        orchestrator = OrchestratorAgent()
        allowed = {"file.read_text", "file.copy", "file.write_text", "file.list_directory", "file.get_metadata"}
        registry = ToolRegistry()
        for tool in orchestrator.registry.list():
            if tool.name in allowed:
                registry.register(tool)
        orchestrator.registry = registry
        return orchestrator

    coordinator = ButlerCoordinator()
    goal = (f"请读取 {source}，将完整内容保存到 {target}，核验两份文件内容完全一致。"
            "源文件已经存在，目标文件目前不存在。请保留源文件，不需要再询问信息。")
    started = time.monotonic()
    matter = None
    with patch.object(PolicyEngine, "__init__", fixed_clock), patch.object(OSExecutionEngine, "_new_orchestrator", fixture_orchestrator):
        try:
            response = await coordinator.message(ButlerMessage(message=goal))
            matter_id = response["matter"]["id"]
            while time.monotonic() - started < timeout:
                await coordinator.tick()
                matter = store.get(ButlerMatter, matter_id)
                if matter.status in {MatterStatus.COMPLETED, MatterStatus.WAITING_INPUT, MatterStatus.WAITING_APPROVAL, MatterStatus.FAILED, MatterStatus.CANCELLED}:
                    break
                await asyncio.sleep(0.25)
            matter = store.get(ButlerMatter, matter_id)
            actual = target.read_bytes() if target.exists() else b""
            matched = actual == source.read_bytes()
            approvals = [row for task_id in matter.task_ids for row in db.fetch_many("approvals", "task_id=?", (task_id,))]
            calls = [row for task_id in matter.task_ids for row in db.list_tool_calls_for_task(task_id)]
            writes = [row for row in calls if row.get("status") == "committed" and row["tool_name"] in {"file.copy", "file.write_text"} and not row["args"].get("dry_run", False)]
            fingerprints = Counter(json.dumps({"tool": call["tool_name"], "args": {k: v for k, v in call["args"].items() if k not in {"approval_id", "approved", "dry_run"}}}, sort_keys=True) for call in writes)
            ok = matter.status == MatterStatus.COMPLETED and matched and bool(matter.completion_evidence)
            undo_ok = False
            if ok:
                for task_id in reversed(matter.task_ids):
                    result = await execute_task_rollback(TaskRollbackRequest(task_id=task_id, source=RollbackSource.NATIVE_CONFIRMATION, confirmation_id="owned-fixture-undo", actor="acceptance"))
                    if result.outcome.get("state") not in {"succeeded", "no_changes"}:
                        break
                undo_ok = not target.exists() and source.read_bytes() == content.encode("utf-8")
            usage = list_usage_events(limit=1000)
            return {
                "case": "real_model_scoped_file_copy_verify_undo", "environment": "real_model_real_files",
                "ok": ok and undo_ok, "matter_status": matter.status.value,
                "result_verified": matched, "undo_verified": undo_ok,
                "evidence_count": len(matter.completion_evidence), "run_count": len(matter.run_ids),
                "completion_rate": int(ok), "user_interventions": sum(row["source"] != "autonomy_scope" for row in approvals),
                "automatic_approvals": sum(row["source"] == "autonomy_scope" and bool(row["consumed_at"]) for row in approvals),
                "duplicate_actions": sum(max(0, count - 1) for count in fingerprints.values()),
                "wrong_changes": int(source.read_bytes() != content.encode("utf-8")) + int(target.exists() and not matched),
                "recovery_attempts": matter.continuation_count, "recovery_success_rate": None if not matter.continuation_count else int(ok),
                "model_calls": len(usage), "model_tokens": sum(row.get("usage", {}).get("total_tokens", 0) for row in usage),
                "output_sha256": hashlib.sha256(actual).hexdigest(), "scope_version": scope.version,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "failure_progress": "" if ok and undo_ok else matter.progress,
                "task_statuses": [db.fetch_one("tasks", task_id).get("status") for task_id in matter.task_ids],
                "task_errors": [str(db.fetch_one("tasks", task_id).get("final_summary") or "")[:2000] for task_id in matter.task_ids] if not ok else [],
            }
        finally:
            await coordinator.stop()
            if matter:
                for run_id in matter.run_ids:
                    run_service.cancel_run(run_id)
            await run_service.shutdown_runs(timeout_seconds=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--report", type=Path, default=ROOT / ".tmp/audit-fixes/butler-real-model.json")
    args = parser.parse_args()
    from app.llm.registry import get_effective_settings, invalidate_settings_cache

    configured = get_effective_settings()
    credential = getpass.getpass("Temporary API key: ") if args.prompt_key else configured.api_key
    scratch = Path(tempfile.mkdtemp(prefix="lengrvis-butler-acceptance-"))
    workspace = scratch / "files"
    workspace.mkdir()
    provider = args.provider or configured.provider_name
    if provider == "mock":
        raise SystemExit("Real-model acceptance refuses MockProvider.")
    os.environ.update({
        "LENGRVIS_TEST": "1", "LENGRVIS_DATA_DIR": str(scratch / "data"),
        "LENGRVIS_CONFIG_FILE": str(scratch / "absent.yaml"), "LENGRVIS_ENV_FILE": str(scratch / "absent.env"),
        "LENGRVIS_AUDIT_HMAC_SECRET": secrets.token_hex(32), "LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS": "1",
        "LENGRVIS_ALLOWED_DIRECTORIES": str(workspace), "LENGRVIS_PROVIDER_NAME": provider,
        "LENGRVIS_MODE": "efficiency", "LENGRVIS_BASE_URL": args.base_url or configured.base_url,
        "LENGRVIS_MODEL": args.model or configured.model, "LENGRVIS_API_KEY": credential,
        "LENGRVIS_REQUIRES_OPENAI_AUTH": "true", "LENGRVIS_WIRE_API": "chat_completions",
        "LENGRVIS_ALLOW_MOCK_FALLBACK": "false", "LENGRVIS_TASK_RECORDING_ENABLED": "false",
        "LENGRVIS_LLM_API_MAX_RETRIES": "0", "LENGRVIS_TIMEOUT": "90",
    })
    invalidate_settings_cache()
    report = {"schema": "butler-acceptance/v1", "started_at": datetime.now(UTC).isoformat(), "model": os.environ["LENGRVIS_MODEL"], "ok": False}
    try:
        report.update(asyncio.run(exercise(workspace, args.timeout_seconds)))
    except Exception as exc:  # broad-exception-boundary: sanitized acceptance report, never raw provider bodies.
        report["error_type"] = type(exc).__name__
        report["failure_location"] = [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno} for frame in traceback.extract_tb(exc.__traceback__)[-6:]]
    finally:
        from app.policy.redaction import redact_value
        report = {**report, "task_errors": redact_value(report.get("task_errors", [])), "failure_progress": redact_value(report.get("failure_progress", ""))}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
