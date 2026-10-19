"""Owned-page -> real BrowserHost -> scoped Excel/Word outputs -> native Office.

Default: controlled planner, real execution and applications. --real-model uses
the actual provider throughout. No private pages, existing documents or user
Office instances are modified. All created preview windows are closed on exit.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import html
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]


async def exercise(args, scratch: Path) -> dict:
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.api.routes_browser import ws_router
    from app.core import butler_store as store, db
    from app.core.butler_models import (
        AutonomyScope,
        ButlerMatter,
        ButlerMessage,
        MatterStatus,
    )
    from app.core.schemas import Plan, PlanStep
    from app.llm.usage import list_usage_events
    from app.orchestration.os_execution_engine import OSExecutionEngine
    from app.policy.policy_engine import PolicyEngine
    from app.security.desktop_api import desktop_api_token
    from app.services import browser_activity_runtime, run_service
    from app.services.browser_host_bridge_service import browser_host_bridge_hub
    from app.services.butler_service import ButlerCoordinator
    from app.services.office_artifacts import verify_artifact
    from app.tools.registry import ToolRegistry

    workspace = scratch / "files"
    workspace.mkdir()
    os.environ["LENGRVIS_ALLOWED_DIRECTORIES"] = str(workspace)
    db.init_db()
    app = FastAPI()
    app.include_router(ws_router, prefix="/api")
    texts = {
        "chinese": "青山项目，负责人林晓，数量12，备注：中文与 emoji 😀。",
        "literal": "原文字符串 =SUM(A1:A2)，可选资料未提供。",
        "long": "资料说明：" + "本段为公开测试资料中的中文说明。" * 80,
        "second": "补充来源，负责人王宁，数量23。",
    }

    @app.get("/fixture/{key}", response_class=HTMLResponse)
    async def page(key: str):
        return (
            '<!doctype html><meta charset="utf-8"><title>公开样例</title><article>'
            + html.escape(texts[key])
            + "</article>"
        )

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0.02)
    child_env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"ELECTRON_RUN_AS_NODE", "LENGRVIS_API_KEY"}
    }
    child_env.update(
        MAVRIS_NATIVE_APP_ACCEPTANCE="1",
        BUTLER_FIXTURE_BASE=base,
        BUTLER_FIXTURE_TOKEN=desktop_api_token(),
        BUTLER_FIXTURE_PROFILE=str(scratch / "browser"),
    )
    log = (scratch / "electron.log").open("w", encoding="utf-8")
    child = subprocess.Popen(
        [
            str(ROOT / "desktop/node_modules/electron/dist/electron.exe"),
            str(ROOT / "desktop/scripts/butler-flow-host.cjs"),
        ],
        cwd=ROOT,
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=log,
        stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    coordinator = None
    reports = []
    window_handles = set()
    matters = []
    original_url = browser_activity_runtime._validate_url
    original_policy = PolicyEngine.__init__

    def fixture_url(url):
        return (
            url
            if url in {base + "/fixture/" + key for key in texts}
            else original_url(url)
        )

    def daytime(self, *values, **kwargs):
        original_policy(
            self,
            *values,
            **{**kwargs, "now_provider": lambda: datetime(2026, 9, 22, 12, tzinfo=UTC)},
        )

    try:
        started = time.monotonic()
        while not browser_host_bridge_hub.status()["connected"]:
            if child.poll() is not None or time.monotonic() - started > 20:
                raise RuntimeError("Owned BrowserHost did not connect")
            await asyncio.sleep(0.05)
        cases = [
            (
                "chinese",
                ["项目", "负责人", "数量"],
                [("chinese", ["青山", "林晓", 12])],
            ),
            ("literal", ["原文", "可选值"], [("literal", ["=SUM(A1:A2)", None])]),
            ("long", ["摘录"], [("long", [texts["long"]]), ("second", ["补充来源"])]),
        ]
        for name, headers, source_rows in cases:
            directory = workspace / name
            directory.mkdir()
            xlsx, docx = directory / "资料.xlsx", directory / "报告.docx"
            rows = [
                {
                    "values": values,
                    "source_url": base + "/fixture/" + key,
                    "quote": texts[key],
                }
                for key, values in source_rows
            ]
            urls = list(dict.fromkeys(row["source_url"] for row in rows))
            store.save(
                AutonomyScope(
                    label="自有验收资料",
                    directories=[str(directory)],
                    origins=[base],
                    allowed_actions=["observe", "file_modify"],
                )
            )
            before_usage = len(list_usage_events(limit=10000))

            class ScriptedPlanner:
                async def create_plan(self, task_id, goal, mode, tools, **kwargs):
                    task_row = db.fetch_one("tasks", task_id)
                    current = store.get(
                        ButlerMatter, task_row["metadata"]["butler_matter_id"]
                    )
                    existing = next(
                        (
                            a
                            for a in current.artifacts
                            if a.kind == "xlsx" and a.state == "verified"
                        ),
                        None,
                    )

                    def step(identity, tool, values, dependencies=None, bindings=None):
                        owner = (
                            "BrowserAgent"
                            if tool.startswith("browser.")
                            else "AppAgent"
                            if tool.startswith("app.")
                            else "DocumentAgent"
                        )
                        return PlanStep(
                            id=identity,
                            task_id=task_id,
                            agent_name=owner,
                            tool_name=tool,
                            description=identity,
                            args=values,
                            depends_on=dependencies or [],
                            input_bindings=bindings or {},
                        )

                    steps = []
                    from app.services.office_artifacts import planning_readbacks

                    readbacks = planning_readbacks(current)["observations"]
                    observed = {
                        item["data"].get("url"): item["data"] for item in readbacks
                    }
                    if not existing:
                        reads = [
                            step(f"source-{index}", "browser.read_page", {"url": url})
                            for index, url in enumerate(urls)
                            if url not in observed
                        ]
                        steps += reads
                        if name == "chinese" and reads:
                            return Plan(task_id=task_id, goal=goal, steps=steps)
                        sourced_rows = [
                            {**row, "quote": observed[row["source_url"]]["text"]}
                            if row["source_url"] in observed
                            else row
                            for row in rows
                        ]
                        steps.append(
                            step(
                                "workbook",
                                "document.create_xlsx",
                                {
                                    "path": str(xlsx),
                                    "title": "资料整理",
                                    "headers": headers,
                                    "rows": sourced_rows,
                                    "dry_run": True,
                                },
                                [s.id for s in reads],
                            )
                        )
                        # Force one case across an actual durable run boundary.
                        if name == "long":
                            return Plan(task_id=task_id, goal=goal, steps=steps)
                    bound = (
                        {}
                        if existing
                        else {"path": {"step_id": "workbook", "path": ["path"]}}
                    )
                    steps.append(
                        step(
                            "read-saved",
                            "document.read_artifact",
                            {"path": existing.path} if existing else {},
                            [] if existing else ["workbook"],
                            bound,
                        )
                    )
                    if name == "chinese" and not any(
                        item["tool"] == "document.read_artifact" for item in readbacks
                    ):
                        return Plan(task_id=task_id, goal=goal, steps=steps)
                    steps.append(
                        step(
                            "report",
                            "document.create_docx",
                            {
                                "path": str(docx),
                                "title": "资料汇总报告",
                                "summary": "以下内容整理自指定公开资料，具体原文与来源见表格。",
                                "dry_run": True,
                            },
                            ["read-saved"],
                            {
                                "source_path": {
                                    "step_id": "read-saved",
                                    "path": ["path"],
                                }
                            },
                        )
                    )
                    steps.append(
                        step(
                            "open-excel",
                            "app.open_file",
                            {"application": "excel"},
                            ["report", "read-saved"],
                            {"path": {"step_id": "read-saved", "path": ["path"]}},
                        )
                    )
                    steps.append(
                        step(
                            "open-word",
                            "app.open_file",
                            {"application": "word"},
                            ["report", "open-excel"],
                            {"path": {"step_id": "report", "path": ["path"]}},
                        )
                    )
                    return Plan(task_id=task_id, goal=goal, steps=steps)

            def factory(_):
                orchestrator = OrchestratorAgent()
                registry = ToolRegistry()
                for tool in orchestrator.registry.list():
                    if tool.name in {
                        "browser.read_page",
                        "browser.observe",
                        "document.create_xlsx",
                        "document.read_artifact",
                        "document.create_docx",
                        "app.open_file",
                        "file.list_directory",
                        "file.get_metadata",
                    }:
                        registry.register(tool)
                orchestrator.registry = registry
                if not args.real_model:
                    orchestrator.planner = ScriptedPlanner()
                return orchestrator

            async def decide(_):
                return {
                    "action": "execute",
                    "completion_conditions": [{"kind": "office_artifacts"}],
                }

            coordinator = ButlerCoordinator(
                decision_provider=None if args.real_model else decide
            )
            began = time.monotonic()
            with (
                patch.object(OSExecutionEngine, "_new_orchestrator", factory),
                patch.object(PolicyEngine, "__init__", daytime),
                patch.object(browser_activity_runtime, "_validate_url", fixture_url),
            ):
                message = (
                    f"读取这些指定网页：{', '.join(urls)}。整理为新的 Excel {xlsx}，再根据实际保存的表格生成 Word {docx}。"
                    f"表格列为：{headers}。保持原文和数字，缺失值留空，保留来源网址；公式样式文本作为普通文本。"
                    "核验保存的内容，并在 Excel、Word 中打开成果。目录已授权。"
                )
                response = await coordinator.message(ButlerMessage(message=message))
                identity = response["matter"]["id"]
                matters.append(identity)
                while time.monotonic() - began < args.timeout:
                    await coordinator.tick()
                    matter = store.get(ButlerMatter, identity)
                    if matter.status in {
                        MatterStatus.COMPLETED,
                        MatterStatus.FAILED,
                        MatterStatus.CANCELLED,
                        MatterStatus.WAITING_INPUT,
                        MatterStatus.WAITING_APPROVAL,
                    }:
                        break
                    await asyncio.sleep(0.05)
                matter = store.get(ButlerMatter, identity)
                calls = [
                    call
                    for task_id in matter.task_ids
                    for call in db.list_tool_calls_for_task(task_id)
                ]
                for call in calls:
                    for output in db.fetch_many(
                        "tool_results", "tool_call_id=?", (call["id"],)
                    ):
                        handle = output.get("output", {}).get("window_handle")
                        if handle:
                            window_handles.add(int(handle))
                checks = []
                for artifact in matter.artifacts:
                    try:
                        projection = verify_artifact(
                            matter, artifact, require_open=True
                        )
                        expected_rows = [r["values"] for r in rows]
                        actual_rows = (
                            [r[:-2] for r in projection["整理数据"][1:]]
                            if artifact.kind == "xlsx"
                            else None
                        )
                        checks.append(
                            actual_rows == expected_rows
                            if actual_rows is not None
                            else bool(projection["tables"])
                        )
                    except (ValueError, OSError):
                        checks.append(False)
                writes = [
                    c
                    for c in calls
                    if c["tool_name"]
                    in {"document.create_xlsx", "document.create_docx"}
                    and c["status"] == "committed"
                    and not c.get("dry_run")
                ]
                approvals = [
                    a
                    for task_id in matter.task_ids
                    for a in db.fetch_many("approvals", "task_id=?", (task_id,))
                ]
                all_usage = list_usage_events(limit=10000)
                usage = all_usage[: max(0, len(all_usage) - before_usage)]
                reports.append(
                    {
                        "case": name,
                        "ok": matter.status == MatterStatus.COMPLETED
                        and len(checks) == 2
                        and all(checks)
                        and len(writes) == 2
                        and all(a["source"] == "autonomy_scope" for a in approvals)
                        and (not args.real_model or bool(usage)),
                        "status": matter.status,
                        "artifacts_verified": checks,
                        "run_count": len(matter.run_ids),
                        "write_count": len(writes),
                        "extra_interventions": sum(
                            a["source"] != "autonomy_scope" for a in approvals
                        ),
                        "model_calls": len(usage),
                        "elapsed_seconds": round(time.monotonic() - began, 2),
                        "progress": matter.progress,
                        "task_summaries": [
                            db.fetch_one("tasks", task_id).get("final_summary", "")
                            for task_id in matter.task_ids
                        ],
                    }
                )
            await coordinator.stop()
            coordinator = None
            if not reports[-1]["ok"]:
                break
        return {
            "ok": len(reports) == 3 and all(r["ok"] for r in reports),
            "cases": reports,
            "planner": "real" if args.real_model else "controlled",
            "native_electron": True,
            "native_office": True,
            "summary_quality_human_review": "pending"
            if args.real_model
            else "fixture text",
        }
    finally:
        if coordinator:
            await coordinator.stop()
        for identity in matters:
            for run_id in store.get(ButlerMatter, identity).run_ids:
                run_service.cancel_run(run_id)
        await run_service.shutdown_runs(timeout_seconds=5)
        # Close only the exact preview windows returned by this run's Office tools.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        for handle in window_handles:
            user32.PostMessageW(handle, 0x0010, 0, 0)
        if child.poll() is None:
            child.stdin.write(b"stop\n")
            child.stdin.flush()
            try:
                await asyncio.to_thread(child.wait, 10)
            except subprocess.TimeoutExpired:
                child.terminate()
        log.close()
        server.should_exit = True
        await server_task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--provider")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--wire-api", choices=["chat_completions", "responses"])
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--report", type=Path, default=ROOT / ".tmp/audit-fixes/butler-office-flow.json"
    )
    args = parser.parse_args()
    from app.llm.registry import get_effective_settings, invalidate_settings_cache
    from scripts.run_butler_coordination_acceptance import preflight

    settings = get_effective_settings()
    settings = settings.model_copy(
        update={
            key: value
            for key, value in {
                "provider_name": args.provider,
                "base_url": args.base_url,
                "model": args.model,
                "wire_api": args.wire_api,
            }.items()
            if value is not None
        }
    )
    configuration = (
        preflight(settings) if args.real_model or args.preflight_only else None
    )
    report = {
        "ok": False,
        "started_at": datetime.now(UTC).isoformat(),
        "preflight": configuration,
        "real_model": args.real_model,
        "acceptance_completed": False,
    }
    if args.preflight_only or (configuration and not configuration["ok"]):
        report["reason"] = (
            "preflight_only" if configuration["ok"] else configuration["reason"]
        )
    else:
        scratch = Path(tempfile.mkdtemp(prefix="butler-office-"))
        # Explicit owned-fixture environment, never persisted user settings.
        os.environ.update(
            LENGRVIS_TEST="1",
            LENGRVIS_DATA_DIR=str(scratch / "data"),
            LENGRVIS_CONFIG_FILE=str(scratch / "absent.yaml"),
            LENGRVIS_ENV_FILE=str(scratch / "absent.env"),
            LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS="1",
            LENGRVIS_AUDIT_HMAC_SECRET=secrets.token_hex(32),
            LENGRVIS_PROVIDER_NAME=settings.provider_name
            if args.real_model
            else "mock",
            LENGRVIS_MODE="efficiency",
            LENGRVIS_API_KEY=settings.api_key if args.real_model else "",
            LENGRVIS_BASE_URL=settings.base_url,
            LENGRVIS_MODEL=settings.model,
            LENGRVIS_WIRE_API=settings.wire_api,
            LENGRVIS_ALLOW_MOCK_FALLBACK="false",
            LENGRVIS_ALLOW_BROWSER_NETWORK="true",
            LENGRVIS_ALLOW_BROWSER_WRITES="true",
            LENGRVIS_TASK_RECORDING_ENABLED="false",
            LENGRVIS_LLM_API_MAX_RETRIES="0",
        )
        invalidate_settings_cache()
        try:
            report.update(asyncio.run(exercise(args, scratch)))
            report["acceptance_completed"] = report["ok"]
        except Exception as exc:  # noqa: BLE001 - report type and code location, never raw credential-bearing provider errors.
            import traceback

            report.update(
                error_type=type(exc).__name__,
                location=[
                    f"{Path(f.filename).name}:{f.lineno}"
                    for f in traceback.extract_tb(exc.__traceback__)[-6:]
                ],
            )
        report["fixture_directory"] = str(scratch)
    from app.policy.redaction import redact_value

    args.report.parent.mkdir(parents=True, exist_ok=True)
    safe_report = redact_value(report)
    # Diagnostic codes are fixed strings, not provider text or credentials.
    if configuration and configuration.get("reason") in {
        "mock_provider_not_allowed",
        "unsupported_provider",
        "credentials_or_parameters_in_base_url",
        "local_endpoint_requires_local_provider",
        "api_key_missing",
        "outbound_url_rejected",
        "model_endpoint_unreachable",
    }:
        safe_report["preflight"]["reason"] = configuration["reason"]
        if report.get("reason") == configuration["reason"]:
            safe_report["reason"] = configuration["reason"]
    args.report.write_text(
        json.dumps(safe_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"ok": report["ok"], "report": str(args.report)}))
    return 0 if report["ok"] or (args.preflight_only and configuration["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
