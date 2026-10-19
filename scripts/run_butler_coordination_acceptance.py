"""Real-model coordination acceptance with a separate backend worker after restart.

Uses disposable source files and the normal coordinator/engine/tool policy. No
fixed planner, automatic rescue, external writes, or persistent user settings.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def preflight(settings) -> dict:
    """Check the selected transport without sending credentials or model prompts."""
    from app.core.outbound_url import is_local_base_url, pin_outbound_http_url
    from app.llm.registry import CLOUD_PROVIDERS, LOCAL_PROVIDERS

    result = {"ok": False, "provider": settings.provider_name, "model": settings.model}
    provider = settings.provider_name.lower()
    if provider == "mock":
        return {**result, "reason": "mock_provider_not_allowed"}
    if provider not in CLOUD_PROVIDERS | LOCAL_PROVIDERS:
        return {**result, "reason": "unsupported_provider"}
    try:
        endpoint = urlsplit(settings.base_url)
        if (
            endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            return {**result, "reason": "credentials_or_parameters_in_base_url"}
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        result.update(host=endpoint.hostname, port=port)
        local = is_local_base_url(settings.base_url)
        if local and provider in CLOUD_PROVIDERS:
            return {**result, "reason": "local_endpoint_requires_local_provider"}
        pinned = pin_outbound_http_url(
            settings.base_url, allow_private=local and provider in LOCAL_PROVIDERS
        )
        if (
            provider in CLOUD_PROVIDERS or settings.requires_openai_auth
        ) and not settings.api_key:
            return {**result, "reason": "api_key_missing"}
        with socket.create_connection((urlsplit(pinned.url).hostname, port), timeout=5):
            pass
    except ValueError:
        return {**result, "reason": "outbound_url_rejected"}
    except OSError as exc:
        return {
            **result,
            "reason": "model_endpoint_unreachable",
            "error_type": type(exc).__name__,
        }
    return {**result, "ok": True}


async def worker(phase: str, workspace: Path, timeout: int) -> dict:
    from app.core import butler_store as store
    from app.core import db
    from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus
    from app.llm.usage import list_usage_events
    from app.services import run_service
    from app.services.butler_service import ButlerCoordinator

    db.init_db()
    coordinator = ButlerCoordinator()
    failures = []
    real_decide = coordinator.decide

    async def observed_decide(matter):
        try:
            return await real_decide(matter)
        except Exception as exc:  # noqa: BLE001 - record location only, never provider bodies or credentials.
            failures.append(
                {
                    "error_type": type(exc).__name__,
                    "location": [
                        f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                        for frame in traceback.extract_tb(exc.__traceback__)[-4:]
                    ],
                }
            )
            raise

    coordinator.decide = observed_decide
    manifest_path = workspace / "manifest.json"
    recovery_notifications = None

    def summary_ids():
        with db.connect() as conn:
            return {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM agent_messages WHERE id LIKE 'msg_butler_%' AND id NOT LIKE 'msg_butler_alert_%'"
                )
            }

    if phase == "prepare":
        store.set_control(coordination_enabled=True)
        scope = store.save(
            AutonomyScope(
                label="统筹验收",
                directories=[str(workspace)],
                allowed_actions=["observe", "file_modify"],
            )
        )
        (workspace / "source-a.txt").write_text(
            "项目：青山\n编号：ALPHA-17\n", encoding="utf-8"
        )
        (workspace / "source-b.txt").write_text(
            "负责人：林晓\n交付：资料汇总与报告\n", encoding="utf-8"
        )
        sources = {
            name: (workspace / name).read_text(encoding="utf-8")
            for name in ("source-a.txt", "source-b.txt")
        }
        parent = store.save(
            ButlerMatter(
                goal=f"读取 {workspace / 'source-a.txt'} 和 {workspace / 'source-b.txt'}，按顺序拼接全部原文保存到 {workspace / 'summary.txt'}，保留两份源文件并核验内容。",
                scope_versions={scope.id: 1},
            )
        )
        child = store.save(
            ButlerMatter(
                goal=f"读取 {workspace / 'summary.txt'}，生成中文 Markdown 报告保存到 {workspace / 'report.md'}。保留项目、编号、负责人和交付四项真实信息，核验保存结果。",
                depends_on=[parent.id],
                scope_versions={scope.id: 1},
            )
        )
        await coordinator.control_matter(parent.id, "pause")
        urgent = store.save(
            ButlerMatter(
                goal=f"将文字‘紧急事项已处理’保存到 {workspace / 'urgent.txt'}，核验文件内容。",
                due_at=(datetime.now(UTC) + timedelta(minutes=20)).isoformat(),
                priority=100,
                priority_explicit=True,
                scope_versions={scope.id: 1},
            )
        )
        manifest = {
            "parent": parent.id,
            "child": child.id,
            "urgent": urgent.id,
            "sources": sources,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pending_before = sum(
            len(item.pending_notifications)
            for item in store.list_records(ButlerMatter, limit=None)
        )
        summaries_before = summary_ids()
        await coordinator.recover()
        summaries_after = summary_ids()
        pending_after = sum(
            len(item.pending_notifications)
            for item in store.list_records(ButlerMatter, limit=None)
        )
        await coordinator.recover()
        recovery_notifications = {
            "pending_before": pending_before,
            "pending_after": pending_after,
            "summaries_added": len(summaries_after - summaries_before),
            "repeated_recovery_deduplicated": summary_ids() == summaries_after,
        }
        child = store.get(ButlerMatter, manifest["child"])
        if (
            child.run_ids
            or store.get(ButlerMatter, manifest["parent"]).status != MatterStatus.PAUSED
        ):
            return {"ok": False, "error": "dependency_or_pause_not_preserved"}
        await coordinator.control_matter(manifest["parent"], "resume")
    deadline = time.monotonic() + timeout
    observed = []
    dependency_order_valid = True
    try:
        while time.monotonic() < deadline:
            await coordinator.tick()
            items = {
                key: store.get(ButlerMatter, manifest[key])
                for key in ("parent", "child", "urgent")
            }
            states = {key: item.status.value for key, item in items.items()}
            if not observed or observed[-1] != states:
                observed.append(states)
            if items["child"].run_ids and not (
                items["parent"].status == MatterStatus.COMPLETED
                and items["parent"].completion_evidence
                and items["child"].dependency_revisions.get(items["parent"].id)
                == items["parent"].outcome_revision
            ):
                dependency_order_valid = False
            if phase == "prepare" and items["urgent"].status == MatterStatus.COMPLETED:
                break
            if phase == "resume" and all(
                item.status == MatterStatus.COMPLETED for item in items.values()
            ):
                break
            if any(
                item.status
                in {
                    MatterStatus.WAITING_INPUT,
                    MatterStatus.WAITING_APPROVAL,
                    MatterStatus.FAILED,
                }
                for item in items.values()
            ):
                break
            await asyncio.sleep(0.2)
        items = {
            key: store.get(ButlerMatter, manifest[key])
            for key in ("parent", "child", "urgent")
        }
        phases = {key: item.status.value for key, item in items.items()}
        usage = list_usage_events(limit=1000)
        approvals = [
            row
            for item in items.values()
            for task in item.task_ids
            for row in db.fetch_many("approvals", "task_id=?", (task,))
        ]
        writes = [
            call
            for item in items.values()
            for task in item.task_ids
            for call in db.list_tool_calls_for_task(task)
            if call.get("status") == "committed"
            and call.get("tool_name")
            in {"file.write_text", "file.write_json", "file.copy"}
        ]
        fingerprints = Counter(
            json.dumps(
                {
                    "tool": call["tool_name"],
                    "args": {
                        key: value
                        for key, value in call["args"].items()
                        if key not in {"approval_id", "approved", "dry_run"}
                    },
                },
                sort_keys=True,
            )
            for call in writes
        )
        sources_ok = all(
            (workspace / name).read_text(encoding="utf-8") == text
            for name, text in manifest["sources"].items()
        )
        summary = (
            (workspace / "summary.txt").read_text(encoding="utf-8-sig")
            if (workspace / "summary.txt").exists()
            else ""
        )
        report = (
            (workspace / "report.md").read_text(encoding="utf-8-sig")
            if (workspace / "report.md").exists()
            else ""
        )
        urgent_ok = (workspace / "urgent.txt").exists() and (
            workspace / "urgent.txt"
        ).read_text(encoding="utf-8-sig").strip() == "紧急事项已处理"
        complete = all(item.status == MatterStatus.COMPLETED for item in items.values())
        artifact_ok = summary == "".join(manifest["sources"].values()) and all(
            value in report for value in ("青山", "ALPHA-17", "林晓", "资料汇总与报告")
        )
        prepare_ok = (
            phases["parent"] == "paused"
            and not items["child"].run_ids
            and phases["urgent"] == "completed"
        )
        duplicates = sum(max(0, count - 1) for count in fingerprints.values())
        recovery_ok = recovery_notifications is None or (
            recovery_notifications["pending_after"] == 0
            and recovery_notifications["summaries_added"]
            == int(recovery_notifications["pending_before"] > 0)
            and recovery_notifications["repeated_recovery_deduplicated"]
        )
        return {
            "ok": (prepare_ok if phase == "prepare" else complete and artifact_ok)
            and sources_ok
            and urgent_ok
            and dependency_order_valid
            and recovery_ok
            and duplicates == 0
            and bool(usage),
            "phase": phase,
            "matter_statuses": phases,
            "observed_states": observed,
            "sources_preserved": sources_ok,
            "artifacts_verified": artifact_ok,
            "urgent_artifact_verified": urgent_ok,
            "dependency_order_valid": dependency_order_valid,
            "recovery_notifications": recovery_notifications,
            "pending_notification_count": sum(
                len(item.pending_notifications) for item in items.values()
            ),
            "summary_notification_count": len(summary_ids()),
            "model_calls": len(usage),
            "model_tokens": sum(
                row.get("usage", {}).get("total_tokens", 0) for row in usage
            ),
            "necessary_confirmations": sum(
                row.get("source") != "autonomy_scope" for row in approvals
            )
            + sum(item.status == MatterStatus.WAITING_INPUT for item in items.values()),
            "rescue_interventions": 0,
            "planned_pause_and_resume": True,
            "duplicate_actions": duplicates,
            "wrong_source_changes": int(not sources_ok),
            "provider_failures": failures,
            "execution_counts": {key: len(item.run_ids) for key, item in items.items()},
        }
    finally:
        await coordinator.stop()
        await run_service.shutdown_runs(timeout_seconds=5)
        db.close_thread_connection()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=["prepare", "resume"])
    parser.add_argument("--workspace", type=Path)
    parser.add_argument(
        "--base-url", help="Temporary model endpoint; does not change project settings."
    )
    parser.add_argument(
        "--provider",
        help="Use local for an explicitly selected loopback model service.",
    )
    parser.add_argument("--model")
    parser.add_argument("--wire-api", choices=["chat_completions", "responses"])
    parser.add_argument(
        "--prompt-key",
        action="store_true",
        help="Read a temporary key in a hidden terminal prompt.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Check configuration without model calls.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / ".tmp/audit-fixes/butler-coordination-real-model.json",
    )
    args = parser.parse_args()
    if args.worker:
        try:
            report = asyncio.run(
                worker(args.worker, args.workspace, args.timeout_seconds)
            )
        except Exception as exc:  # noqa: BLE001 - sanitized acceptance boundary.
            report = {"ok": False, "error_type": type(exc).__name__}
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0 if report["ok"] else 1
    from app.llm.registry import get_effective_settings

    configured = get_effective_settings()
    if args.prompt_key and not sys.stdin.isatty():
        parser.error(
            "--prompt-key requires an interactive terminal; otherwise use the project settings or environment."
        )
    overrides = {
        field: value
        for field, value in {
            "base_url": args.base_url,
            "provider_name": args.provider,
            "model": args.model,
            "wire_api": args.wire_api,
        }.items()
        if value is not None
    }
    if args.prompt_key:
        overrides["api_key"] = getpass.getpass("Temporary API key: ")
    configured = configured.model_copy(update=overrides)
    configuration = preflight(configured)
    report = {
        "schema": "butler-coordination-acceptance/v2",
        "environment": "real_model_process_restart",
        "ok": False,
        "started_at": datetime.now(UTC).isoformat(),
        "preflight": configuration,
        "acceptance_completed": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if not configuration["ok"]:
        report["reason"] = configuration["reason"]
    elif args.preflight_only:
        report["reason"] = "configuration_only_model_acceptance_not_run"
    else:
        with tempfile.TemporaryDirectory(prefix="lengrvis-coordination-") as scratch:
            root = Path(scratch)
            workspace = root / "files"
            workspace.mkdir()
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("LENGRVIS_")
            }
            # Preserve an existing explicit operator opt-in; never enable it implicitly.
            if "LENGRVIS_TRUSTED_FAKE_IP_TUN" in os.environ:
                env["LENGRVIS_TRUSTED_FAKE_IP_TUN"] = os.environ[
                    "LENGRVIS_TRUSTED_FAKE_IP_TUN"
                ]
            env.update(
                {
                    "PYTHONUTF8": "1",
                    "LENGRVIS_TEST": "1",
                    "LENGRVIS_DATA_DIR": str(root / "data"),
                    "LENGRVIS_CONFIG_FILE": str(root / "absent.yaml"),
                    "LENGRVIS_ENV_FILE": str(root / "absent.env"),
                    "LENGRVIS_AUDIT_HMAC_SECRET": secrets.token_hex(32),
                    "LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS": "1",
                    "LENGRVIS_ALLOWED_DIRECTORIES": str(workspace),
                    "LENGRVIS_PROVIDER_NAME": configured.provider_name,
                    "LENGRVIS_MODE": "efficiency",
                    "LENGRVIS_BASE_URL": configured.base_url,
                    "LENGRVIS_MODEL": configured.model,
                    "LENGRVIS_API_KEY": configured.api_key,
                    "LENGRVIS_WIRE_API": configured.wire_api,
                    "LENGRVIS_MODEL_REASONING_EFFORT": configured.model_reasoning_effort,
                    "LENGRVIS_DISABLE_RESPONSE_STORAGE": str(
                        configured.disable_response_storage
                    ).lower(),
                    "LENGRVIS_REQUIRES_OPENAI_AUTH": str(
                        configured.requires_openai_auth
                    ).lower(),
                    "LENGRVIS_ALLOW_MOCK_FALLBACK": "false",
                    "LENGRVIS_TASK_RECORDING_ENABLED": "false",
                    "LENGRVIS_LLM_API_MAX_RETRIES": "0",
                    "LENGRVIS_TIMEOUT": str(min(90, args.timeout_seconds)),
                }
            )
            phases = []
            for phase in ("prepare", "resume"):
                output = root / f"{phase}.json"
                try:
                    result = subprocess.run(
                        [
                            sys.executable,
                            __file__,
                            "--worker",
                            phase,
                            "--workspace",
                            str(workspace),
                            "--report",
                            str(output),
                            "--timeout-seconds",
                            str(args.timeout_seconds),
                        ],
                        env=env,
                        cwd=ROOT,
                        capture_output=True,
                        timeout=args.timeout_seconds + 45,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    phases.append(
                        {"ok": False, "phase": phase, "error_type": "TimeoutExpired"}
                    )
                    break
                phases.append(
                    json.loads(output.read_text(encoding="utf-8"))
                    if output.exists()
                    else {"ok": False, "exit_code": result.returncode}
                )
                if not (workspace / "manifest.json").exists():
                    break
            report.update(
                phases=phases,
                restarted=len(phases) == 2,
                ok=len(phases) == 2 and all(item["ok"] for item in phases),
                acceptance_completed=len(phases) == 2
                and all(item["ok"] for item in phases),
            )
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({"ok": report["ok"], "report": str(args.report)}, ensure_ascii=False)
    )
    return 0 if report["ok"] or (args.preflight_only and configuration["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
