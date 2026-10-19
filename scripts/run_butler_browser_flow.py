"""Full real-file -> backend -> signed Electron form -> receipt -> file acceptance.

Default uses a scripted decision/planner with real execution. --real-model uses
the configured model. Only owned local pages and disposable files are reachable.
"""

import argparse
import asyncio
import base64
import getpass
import html
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend"), str(ROOT / "backend/tests")]


async def exercise(args, scratch: Path) -> dict:
    import httpx
    import uvicorn
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.api.routes_approvals import router as approvals_router
    from app.api.routes_browser import ws_router
    from app.api.routes_butler import router as butler_router
    from app.core import butler_store as store
    from app.core import db
    from app.core.butler_models import ButlerMatter, ButlerMessage, MatterStatus
    from app.core.schemas import Plan, PlanStep
    from app.llm.usage import list_usage_events
    from app.orchestration.os_execution_engine import OSExecutionEngine
    from app.policy.policy_engine import PolicyEngine
    from app.security.desktop_api import desktop_api_token
    from app.services import browser_activity_runtime, run_service
    from app.services.browser_host_bridge_service import browser_host_bridge_hub
    from app.services.butler_evidence import undo_available
    from app.services.butler_service import ButlerCoordinator
    from app.tools.browser_tools import reset_browser_activity_runtime
    from app.tools.registry import ToolRegistry
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from native_confirmation_helpers import signed_native_confirmation_headers

    db.init_db()
    workspace = scratch / "files"
    workspace.mkdir()
    reference = "FLOW-" + secrets.token_hex(4)
    expected = {
        "reference": reference,
        "date": "2026-10-02",
        "notes": "两份资料已合并，保留中文与 emoji 😀",
    }
    left, right, output = (
        workspace / name for name in ("request.json", "details.json", "receipt.json")
    )
    left.write_text(
        json.dumps({"fields": {"reference": reference}}, ensure_ascii=False),
        encoding="utf-8",
    )
    right.write_text(
        json.dumps(
            {"fields": {"date": expected["date"], "notes": expected["notes"]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_bytes = {path: path.read_bytes() for path in (left, right)}
    os.environ["LENGRVIS_ALLOWED_DIRECTORIES"] = str(workspace)
    key = Ed25519PrivateKey.generate()
    os.environ["LENGRVIS_NATIVE_CONFIRMATION_PUBLIC_KEY"] = (
        base64.urlsafe_b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )
        .decode()
        .rstrip("=")
    )
    from app.llm.registry import invalidate_settings_cache

    invalidate_settings_cache()
    token = desktop_api_token()
    app = FastAPI()
    submissions = []
    fixture_requests = []
    session_ids = set()
    cache_resets = 0

    @app.middleware("http")
    async def owned_api(request: Request, next_handler):
        if (
            request.url.path.startswith("/api/")
            and request.headers.get("x-lengrvis-desktop-token") != token
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = await next_handler(request)
        if request.url.path.startswith("/fixture/"):
            fixture_requests.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                }
            )
        return response

    app.include_router(ws_router, prefix="/api")
    app.include_router(butler_router, prefix="/api")
    app.include_router(approvals_router, prefix="/api")

    @app.get("/fixture/form", response_class=HTMLResponse)
    async def form():
        return '<!doctype html><meta charset="utf-8"><title>Owned workflow form</title><h1>资料提交</h1><form id="flow-form" method="post" action="/fixture/submit"><label>业务引用<input id="reference" name="reference" required></label><label>日期<input id="date" name="date" type="date" required></label><label>正文<textarea id="notes" name="notes" required></textarea></label><button type="submit">提交示例资料</button></form>'

    @app.post("/fixture/submit", response_class=HTMLResponse)
    async def submitted(request: Request):
        values = {
            name: items[0]
            for name, items in parse_qs(
                (await request.body()).decode(), keep_blank_values=True
            ).items()
        }
        submissions.append(values)
        if args.scenario == "receipt_delay":
            await asyncio.sleep(3)
        fields = {**values, "status": "confirmed", "id": f"receipt-{len(submissions)}"}
        return '<!doctype html><meta charset="utf-8"><title>业务回执</title>' + "".join(
            f'<p id="receipt-{name}">{html.escape(value)}</p>'
            for name, value in fields.items()
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
        BUTLER_FIXTURE_TOKEN=token,
        BUTLER_FIXTURE_PROFILE=str(scratch / "browser"),
    )
    child_log = (scratch / "electron.log").open("w", encoding="utf-8")
    child = subprocess.Popen(
        [
            str(ROOT / "desktop/node_modules/electron/dist/electron.exe"),
            str(ROOT / "desktop/scripts/butler-flow-host.cjs"),
        ],
        cwd=ROOT,
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=child_log,
        stderr=child_log,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    started = time.monotonic()
    while (
        not browser_host_bridge_hub.status()["connected"]
        and time.monotonic() - started < 15
    ):
        if child.poll() is not None:
            raise RuntimeError("Fixture browser exited before connection")
        await asyncio.sleep(0.05)
    selectors = {
        name: f"#receipt-{name}"
        for name in ("reference", "date", "notes", "status", "id")
    }
    checks = [
        {"label": name, "selector": selectors[name], "expected": value}
        for name, value in {**expected, "status": "confirmed"}.items()
    ]
    checks.append({"label": "id", "selector": selectors["id"], "operator": "nonempty"})
    conditions = [
        {
            "kind": "browser_fields",
            "subject": base + "/fixture/submit",
            "checks": checks,
        },
        {
            "kind": "file_json",
            "subject": str(output),
            "checks": [
                {
                    "label": item["label"],
                    "path": [item["label"], "text"],
                    "operator": item.get("operator", "equals"),
                    "expected": item.get("expected"),
                }
                for item in checks
            ],
        },
    ]
    plan_count = 0

    class ScriptedPlanner:
        async def create_plan(self, task_id, goal, mode, tools, **kwargs):
            nonlocal plan_count, cache_resets
            plan_count += 1

            def step(name, tool, values, deps=None, bindings=None):
                return PlanStep(
                    id=name,
                    task_id=task_id,
                    agent_name="BrowserAgent"
                    if tool.startswith("browser.")
                    else "FileAgent",
                    tool_name=tool,
                    description=name,
                    args=values,
                    depends_on=deps or [],
                    input_bindings=bindings or {},
                )

            if plan_count == 1:
                return Plan(
                    task_id=task_id,
                    goal=goal,
                    steps=[
                        step(
                            "observe-original",
                            "browser.observe",
                            {"url": base + "/fixture/form"},
                        )
                    ],
                )
            if plan_count == 2:
                reset_browser_activity_runtime()
                cache_resets += 1
            prefix = (
                [
                    step("read-a", "file.read_json", {"path": str(left)}),
                    step("read-b", "file.read_json", {"path": str(right)}),
                    step(
                        "merge",
                        "workflow.merge_data",
                        {},
                        ["read-a", "read-b"],
                        {
                            "left": {"step_id": "read-a", "path": ["data"]},
                            "right": {"step_id": "read-b", "path": ["data"]},
                        },
                    ),
                    step(
                        "submit-once",
                        "browser.submit_form",
                        {
                            "url": base + "/fixture/form",
                            "selector": "#flow-form",
                            "dry_run": True,
                        },
                        ["merge"],
                        {"fields": {"step_id": "merge", "path": ["data", "fields"]}},
                    ),
                ]
                if plan_count == 2
                else []
            )
            return Plan(
                task_id=task_id,
                goal=goal,
                steps=[
                    *prefix,
                    step(
                        "wait-receipt",
                        "browser.wait_for_selector",
                        {
                            "url": base + "/fixture/submit",
                            "selector": "#receipt-id",
                            "timeout_ms": 100
                            if args.scenario == "receipt_delay" and plan_count == 2
                            else 5000,
                        },
                        ["submit-once"] if prefix else [],
                    ),
                    step(
                        "read-receipt",
                        "browser.observe",
                        {"url": base + "/fixture/submit", "selectors": selectors},
                        ["wait-receipt"],
                    ),
                    step(
                        "save-receipt",
                        "file.write_json",
                        {"path": str(output), "dry_run": True},
                        ["read-receipt"],
                        {"data": {"step_id": "read-receipt", "path": ["extracted"]}},
                    ),
                ],
            )

    original_policy_init = PolicyEngine.__init__

    def daytime(self, *values, **kwargs):
        kwargs["now_provider"] = lambda: datetime(2026, 9, 17, 12, tzinfo=UTC)
        original_policy_init(self, *values, **kwargs)

    def orchestrator_factory(_):
        orchestrator = OrchestratorAgent()
        registry = ToolRegistry()
        allowed = {
            "file.read_json",
            "file.write_json",
            "file.read_text",
            "file.write_text",
            "workflow.merge_data",
            "browser.observe",
            "browser.submit_form",
            "browser.wait_for_selector",
        }
        for tool in orchestrator.registry.list():
            if tool.name in allowed:
                registry.register(tool)
        orchestrator.registry = registry
        if not args.real_model:
            orchestrator.planner = ScriptedPlanner()
        return orchestrator

    original_url = browser_activity_runtime._validate_url

    def fixture_url(url):
        return (
            url
            if url in {base + "/fixture/form", base + "/fixture/submit"}
            else original_url(url)
        )

    async def decide(_):
        return {"action": "execute", "completion_conditions": conditions}

    coordinator = ButlerCoordinator(
        decision_provider=None if args.real_model else decide
    )
    matter = None
    confirmations = []
    try:
        async with httpx.AsyncClient(
            base_url=base, headers={"x-lengrvis-desktop-token": token}, timeout=60
        ) as client:
            scope = {
                "label": "Owned flow fixture",
                "directories": [str(workspace)],
                "origins": [base],
                "allowed_actions": ["observe", "file_modify", "browser_prepare"],
            }
            challenge = (
                await client.post(
                    "/api/butler/scopes/native-confirmation-challenge", json=scope
                )
            ).json()
            granted = await client.post(
                "/api/butler/scopes",
                json=scope,
                headers=signed_native_confirmation_headers(challenge, key),
            )
            granted.raise_for_status()
            with (
                patch.object(
                    OSExecutionEngine, "_new_orchestrator", orchestrator_factory
                ),
                patch.object(PolicyEngine, "__init__", daytime),
                patch.object(browser_activity_runtime, "_validate_url", fixture_url),
            ):
                goal = f"将 {left} 和 {right} 的 fields 合并后提交到 {base}/fixture/form 的标准 HTML POST 表单，保存实际回执字段到 {output}。业务引用为 {reference}，日期为2026-10-02，状态必须confirmed、回执编号非空。保留原资料，只提交一次。网页回执各字段有 receipt-reference、receipt-date、receipt-notes、receipt-status、receipt-id 的唯一ID，输出JSON保存各字段的实际观察对象。"
                reply = await coordinator.message(ButlerMessage(message=goal))
                matter_id = reply["matter"]["id"]
                while time.monotonic() - started < args.timeout:
                    await coordinator.tick()
                    session_ids.update(
                        item["id"]
                        for item in (
                            browser_host_bridge_hub.status().get("snapshot") or {}
                        ).get("sessions", [])
                    )
                    matter = store.get(ButlerMatter, matter_id)
                    if matter.status == MatterStatus.WAITING_APPROVAL:
                        approval = db.fetch_one(
                            "approvals", matter.interaction.approval_id
                        )
                        if approval["id"] in confirmations:
                            await asyncio.sleep(0.05)
                            continue
                        if (
                            approval["tool_name"] != "browser.submit_form"
                            or submissions
                        ):
                            break
                        challenge_response = await client.post(
                            f"/api/approvals/{approval['id']}/native-confirmation-challenge",
                            json={
                                "action": "approve",
                                "expected_preview_hmac": approval["preview_hmac"],
                            },
                        )
                        challenge_response.raise_for_status()
                        challenge = challenge_response.json()
                        response = await client.post(
                            f"/api/approvals/{approval['id']}/approve",
                            headers=signed_native_confirmation_headers(challenge, key),
                        )
                        response.raise_for_status()
                        confirmations.append(approval["id"])
                    if matter.status in {
                        MatterStatus.COMPLETED,
                        MatterStatus.FAILED,
                        MatterStatus.CANCELLED,
                        MatterStatus.WAITING_INPUT,
                    }:
                        break
                    await asyncio.sleep(0.05)
                matter = store.get(ButlerMatter, matter_id)
                saved = (
                    json.loads(output.read_text(encoding="utf-8"))
                    if output.exists()
                    else {}
                )
                verified = (
                    len(submissions) == 1
                    and submissions[0] == expected
                    and all(
                        saved.get(name, {}).get("text") == value
                        for name, value in {**expected, "status": "confirmed"}.items()
                    )
                    and bool(saved.get("id", {}).get("text"))
                )
                sessions = (browser_host_bridge_hub.status().get("snapshot") or {}).get(
                    "sessions", []
                )
                retained = (
                    len(session_ids) == 1
                    and sum(
                        item["path"] == "/fixture/form" for item in fixture_requests
                    )
                    == 1
                )
                tasks = [db.fetch_one("tasks", task_id) for task_id in matter.task_ids]
                timeouts = sum(
                    bool(task.get("metadata", {}).get("butler_receipt_wait_step"))
                    for task in tasks
                )
                recovery_verified = args.scenario != "receipt_delay" or (
                    timeouts == 1 and len(tasks) == 3
                )
                unchanged = all(
                    path.read_bytes() == value for path, value in source_bytes.items()
                )
                usage = list_usage_events(limit=1000)
                whole_undo = undo_available(matter)
                return {
                    "ok": bool(
                        verified
                        and retained
                        and unchanged
                        and recovery_verified
                        and len(confirmations) == 1
                        and not whole_undo
                        and matter.status == MatterStatus.COMPLETED
                    ),
                    "environment": "real_model_full_stack"
                    if args.real_model
                    else "scripted_planner_real_backend_electron",
                    "scenario": args.scenario,
                    "retained_original_tab": retained,
                    "backend_browser_cache_resets": cache_resets,
                    "source_files_unchanged": unchanged,
                    "whole_matter_undo_available": whole_undo,
                    "receipt_wait_timeouts": timeouts,
                    "recovery_verified": recovery_verified
                    if args.scenario == "receipt_delay"
                    else None,
                    "matter_status": matter.status.value,
                    "run_count": len(matter.run_ids),
                    "native_session_count": len(sessions),
                    "necessary_confirmations": len(confirmations),
                    "rescue_interventions": 0,
                    "submissions": len(submissions),
                    "duplicate_actions": max(0, len(submissions) - 1),
                    "wrong_changes": int(not unchanged)
                    + sum(submission != expected for submission in submissions),
                    "artifact_verified": verified,
                    "model_calls": len(usage),
                    "model_tokens": sum(item.get("total_tokens", 0) for item in usage),
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "fixture_requests": fixture_requests,
                    "progress": matter.progress,
                    "task_summaries": [task.get("final_summary", "") for task in tasks],
                    "evidence": matter.completion_evidence,
                }
    finally:
        await coordinator.stop()
        if matter:
            for run_id in matter.run_ids:
                run_service.cancel_run(run_id)
        await run_service.shutdown_runs(timeout_seconds=5)
        if child.poll() is None:
            child.stdin.write(b"stop\n")
            child.stdin.flush()
            try:
                await asyncio.to_thread(child.wait, 10)
            except subprocess.TimeoutExpired:
                child.terminate()
        child_log.close()
        server.should_exit = True
        await server_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument(
        "--scenario", choices=["normal", "receipt_delay"], default="normal"
    )
    parser.add_argument("--base-url", default="http://localhost:56622/v1")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / ".tmp/audit-fixes/butler-complete-flow.json",
    )
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="butler-flow-"))
    key = (
        getpass.getpass("Temporary API key: ")
        if args.prompt_key
        else os.environ.get("LENGRVIS_API_KEY", "")
    )
    os.environ.update(
        LENGRVIS_TEST="1",
        LENGRVIS_DATA_DIR=str(scratch / "data"),
        LENGRVIS_CONFIG_FILE=str(scratch / "absent.yaml"),
        LENGRVIS_ENV_FILE=str(scratch / "absent.env"),
        LENGRVIS_AUDIT_HMAC_SECRET=secrets.token_hex(32),
        LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS="1",
        LENGRVIS_MODE="efficiency",
        LENGRVIS_PROVIDER_NAME="local" if args.real_model else "mock",
        LENGRVIS_BASE_URL=args.base_url,
        LENGRVIS_MODEL=args.model,
        LENGRVIS_API_KEY=key,
        LENGRVIS_REQUIRES_OPENAI_AUTH="true",
        LENGRVIS_WIRE_API="chat_completions",
        LENGRVIS_ALLOW_BROWSER_NETWORK="true",
        LENGRVIS_ALLOW_MOCK_FALLBACK="false",
        LENGRVIS_TASK_RECORDING_ENABLED="false",
        LENGRVIS_TIMEOUT="90",
    )
    report = {"schema": "butler-complete-flow/v1", "ok": False}
    try:
        report.update(asyncio.run(exercise(args, scratch)))
    except Exception as exc:  # broad-exception-boundary: fixture report excludes provider bodies and credentials.
        report.update(
            error_type=type(exc).__name__,
            failure_location=[
                f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                for frame in traceback.extract_tb(exc.__traceback__)[-5:]
            ],
        )
    from app.policy.redaction import redact_value

    for field in ("progress", "task_summaries", "evidence"):
        if field in report:
            report[field] = redact_value(report[field])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
