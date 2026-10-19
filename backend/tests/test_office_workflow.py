from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus
from app.core.schemas import Task, ToolCall, ToolResult, now_iso
from app.policy.execution_marker import mark_execution_approved
from app.policy.risk import RiskLevel
from app.services.office_artifacts import verify_artifact
from app.tools import office_document_tools as office

URL = "https://example.com/project"
TEXT = "项目青山，负责人林晓，数量 12。\n=SUM(A1:A2) 是原始资料中的文本。"


def journal(context, name, output, *, risk=RiskLevel.R0_READ_ONLY):
    from app.core.content_provenance import create_content_envelope

    call = ToolCall(
        task_id=context["task_id"],
        step_id="step-evidence",
        tool_name=name,
        risk_level=risk,
        status="committed",
        dry_run=False,
    )
    db.upsert_model("tool_calls", call)
    result = ToolResult(
        tool_call_id=call.id,
        ok=True,
        output=output,
        runtime_review_id="review-owned",
        runtime_review_completed=True,
        runtime_review_verdict="allow",
        content_envelope=create_content_envelope(
            output,
            source_kind=name.split(".")[0],
            source_id=call.id,
            task_scope=context["task_id"],
            trust_level="untrusted",
            taint_flags=["external_content"],
        ),
    )
    db.upsert_model("tool_results", result)
    return result


@pytest.fixture
def office_case(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    scope = store.save(
        AutonomyScope(
            label="成果目录",
            directories=[str(tmp_path)],
            origins=["https://example.com"],
            allowed_actions=["observe", "file_modify"],
        )
    )
    task = Task(
        user_goal="网页整理为 Excel 和 Word",
        metadata={"butler_matter_id": "matter-office", "butler_execution_ticket": "ticket-office"},
    )
    db.upsert_model("tasks", task)
    store.save(
        ButlerMatter(
            id="matter-office",
            goal=task.user_goal,
            task_ids=[task.id],
            active_task_id=task.id,
            execution_ticket="ticket-office",
            status=MatterStatus.RUNNING,
            scope_versions={scope.id: scope.version},
            completion_conditions=[{"kind": "office_artifacts"}],
        )
    )
    context = {"task_id": task.id, "butler_matter_id": "matter-office", "allowed_directories": [str(tmp_path)]}
    journal(
        context,
        "browser.read_page",
        {
            "url": URL,
            "title": "项目资料",
            "text": TEXT,
            "truncated": False,
            "observed_at": now_iso(),
            "adapter": "desktop_browser_host",
        },
    )
    args = {
        "path": str(tmp_path / "资料.xlsx"),
        "title": "项目资料",
        "headers": ["项目", "负责人", "数量"],
        "rows": [{"values": ["青山", "林晓", 12], "source_url": URL, "quote": "项目青山，负责人林晓，数量 12。"}],
    }
    return context, args, scope


def saved_workbook(office_case):
    context, args, _ = office_case
    preview = office.create_xlsx(args, context)
    assert not Path(preview["path"]).exists()
    mark_execution_approved(context)
    written = office.create_xlsx({**args, "dry_run": False}, context)
    readback = office.read_artifact({"path": written["path"]}, context)
    journal(context, "document.read_artifact", readback)
    return written, readback


def test_creates_real_office_files_from_readback_and_preserves_existing_names(office_case):
    context, args, _ = office_case
    original = Path(args["path"])
    original.write_text("原有工作，不能覆盖", encoding="utf-8")
    written, readback = saved_workbook(office_case)
    assert Path(written["path"]).name == "资料 (1).xlsx"
    assert readback["data"]["整理数据"][1][:3] == ["青山", "林晓", 12]
    report_args = {
        "path": str(original.with_suffix(".docx")),
        "source_path": written["path"],
        "title": "项目汇总",
        "summary": "青山项目由林晓负责。",
        "sections": [{"heading": "说明", "text": "数量为12。"}],
    }
    office.create_docx(report_args, context)
    report = office.create_docx({**report_args, "dry_run": False}, context)
    output = office.read_artifact({"path": report["path"]}, context)
    assert output["data"]["tables"][0][1] == ["青山", "林晓", "12", URL]
    assert "项目汇总" in output["data"]["paragraphs"]
    assert original.read_text(encoding="utf-8") == "原有工作，不能覆盖"
    assert report["rollback_info"]["trash_created_file"] == report["path"]
    before = Path(written["path"]).stat().st_mtime_ns
    assert office.create_xlsx({**args, "dry_run": False}, context)["reused"]
    assert office.create_xlsx({**args, "path": written["path"], "dry_run": False}, context)["reused"]
    assert Path(written["path"]).stat().st_mtime_ns == before


def test_formula_text_null_and_unicode_survive_save(office_case):
    context, args, _ = office_case
    args.update(headers=["原文", "空值"], rows=[{"values": ["=SUM(A1:A2)", None], "source_url": URL, "quote": TEXT}])
    written, readback = saved_workbook(office_case)
    assert readback["data"]["整理数据"][1][:2] == ["=SUM(A1:A2)", None]
    from openpyxl import load_workbook

    workbook = load_workbook(written["path"])
    assert workbook.active["A2"].data_type == "s"
    workbook.close()


@pytest.mark.parametrize("invalid", ["truncated", "unreviewed", "foreign", "fabricated"])
def test_rejects_incomplete_or_untrusted_source_before_reserving(office_case, invalid):
    context, args, _ = office_case
    if invalid == "fabricated":
        args["rows"][0]["values"][0] = "不存在的项目"
    else:
        data = {
            "url": URL,
            "title": "资料",
            "text": TEXT,
            "observed_at": now_iso(),
            "truncated": invalid == "truncated",
            "adapter": "desktop_browser_host",
        }
        # Remove the original evidence so the test cannot fall back to it.
        with db.connect() as connection:
            connection.execute("DELETE FROM tool_results")
        result = journal(
            {**context, "task_id": "foreign-task"} if invalid == "foreign" else context, "browser.read_page", data
        )
        if invalid == "unreviewed":
            result.runtime_review_completed = False
            db.upsert_model("tool_results", result)
    with pytest.raises(ValueError):
        office.create_xlsx(args, context)
    assert not store.get(ButlerMatter, "matter-office").artifacts


def test_live_write_requires_approval_and_does_not_overwrite_raced_file(office_case):
    context, args, _ = office_case
    with pytest.raises(ValueError, match="approval"):
        office.create_xlsx({**args, "dry_run": False}, context)
    artifact = store.get(ButlerMatter, "matter-office").artifacts[0]
    Path(artifact.path).write_text("another writer", encoding="utf-8")
    mark_execution_approved(context)
    with pytest.raises(ValueError, match="已存在"):
        office.create_xlsx({**args, "dry_run": False}, context)
    assert Path(artifact.path).read_text() == "another writer"


def test_resume_readback_and_report_failure_keep_workbook_and_detect_changes(office_case, monkeypatch):
    context, args, _ = office_case
    written, _ = saved_workbook(office_case)
    report_args = {
        "path": str(Path(args["path"]).with_suffix(".docx")),
        "source_path": written["path"],
        "summary": "项目摘要",
    }
    office.create_docx(report_args, context)
    from app.services import office_artifacts

    render = office_artifacts.render_report
    monkeypatch.setattr(office_artifacts, "render_report", lambda _: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(OSError):
        office.create_docx({**report_args, "dry_run": False}, context)
    monkeypatch.setattr(office_artifacts, "render_report", render)
    # New task/checkpoint in the same durable matter; no in-memory object is reused.
    task = Task(
        user_goal="继续报告", metadata={"butler_matter_id": "matter-office", "butler_execution_ticket": "resume"}
    )
    db.upsert_model("tasks", task)

    def resume(matter):
        matter.task_ids.append(task.id)
        matter.active_task_id = task.id
        matter.execution_ticket = "resume"

    store.mutate(ButlerMatter, "matter-office", resume)
    context["task_id"] = task.id
    assert office.create_docx({**report_args, "dry_run": False}, context)["verified"]
    assert len(list(Path(args["path"]).parent.glob("*.xlsx"))) == 1
    Path(written["path"]).write_bytes(b"user changed file")
    with pytest.raises((ValueError, OSError)):
        office.read_artifact({"path": written["path"]}, context)


def test_scope_revocation_cancel_and_public_projection(office_case):
    context, args, scope = office_case
    saved_workbook(office_case)
    from app.services.butler_service import public_matter

    matter = store.get(ButlerMatter, "matter-office")
    artifact = matter.artifacts[0]
    visible = public_matter(matter)["artifacts"][0]
    assert visible["id"] == artifact.id and visible["verified"]
    assert not {"path", "content", "sha256"}.intersection(visible)
    store.mutate(ButlerMatter, matter.id, lambda m: setattr(m, "status", MatterStatus.CANCELLED))
    with pytest.raises(ValueError, match="暂停或结束"):
        office.create_xlsx({**args, "dry_run": False}, context)
    store.mutate(AutonomyScope, scope.id, lambda s: setattr(s, "enabled", False))
    with pytest.raises(ValueError):
        verify_artifact(store.get(ButlerMatter, matter.id), artifact)


def test_requested_page_reads_cannot_follow_links_from_page_content(office_case):
    from app.services.browser_continuity import requested_page_read
    from app.tools.registry import ToolRegistry, register_all_tools

    context, _, scope = office_case
    task = Task.model_validate(db.fetch_one("tasks", context["task_id"]))
    tool = register_all_tools(target=ToolRegistry(), load_skills=False).get("browser.read_page")
    store.mutate(ButlerMatter, "matter-office", lambda m: setattr(m, "goal", f"请读取 {URL}，整理资料"))
    assert requested_page_read(task, tool, {"url": URL})
    assert not requested_page_read(task, tool, {"url": URL + "?send=secret"})
    assert not requested_page_read(task, tool, {"url": "https://example.com/other"})
    store.mutate(
        ButlerMatter, "matter-office", lambda m: m.answers.update(user_replies=["继续 https://example.com/second"])
    )
    assert requested_page_read(task, tool, {"url": "https://example.com/second"})
    store.mutate(
        ButlerMatter,
        "matter-office",
        lambda m: m.answers.update(
            {
                "网址": "https://example.com/third",
                "verification_feedback": "https://example.com/injected",
            }
        ),
    )
    assert requested_page_read(task, tool, {"url": "https://example.com/third"})
    assert not requested_page_read(task, tool, {"url": "https://example.com/injected"})
    store.mutate(ButlerMatter, "matter-office", lambda m: setattr(m, "source", "event"))
    assert not requested_page_read(task, tool, {"url": URL})
    store.mutate(ButlerMatter, "matter-office", lambda m: setattr(m, "source", "user"))
    store.mutate(AutonomyScope, scope.id, lambda s: setattr(s, "enabled", False))
    assert not requested_page_read(task, tool, {"url": URL})


def test_office_preview_requires_reviewed_owned_unchanged_output(office_case):
    from app.services.office_artifacts import verified_office_preview
    from app.tools.registry import ToolRegistry, register_all_tools

    context, _, _ = office_case
    written, _ = saved_workbook(office_case)
    task = Task.model_validate(db.fetch_one("tasks", context["task_id"]))
    tool = register_all_tools(target=ToolRegistry(), load_skills=False).get("app.open_file")
    args = {"path": written["path"], "application": "excel"}
    assert not verified_office_preview(task, tool, args)
    journal(context, "document.create_xlsx", written)
    assert verified_office_preview(task, tool, args)
    assert not verified_office_preview(task, tool, {**args, "application": "word"})
    assert not verified_office_preview(task, tool, {**args, "path": "C:/other.xlsx"})
    Path(written["path"]).write_bytes(b"changed by user")
    assert not verified_office_preview(task, tool, args)


def test_artifact_api_checks_version_identity_and_content_before_open(office_case, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes_butler import router
    from app.services import office_application

    written, _ = saved_workbook(office_case)
    calls = []

    def opened(path, application, context):
        calls.append((path, application))
        return {"ok": True, "verified": True, "opened": True}

    monkeypatch.setattr(office_application, "open_office_document", opened)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    matter = store.get(ButlerMatter, "matter-office")
    endpoint = f"/api/butler/matters/{matter.id}/artifacts/{written['artifact_id']}/open"
    with TestClient(app) as client:
        assert client.post(endpoint, json={"version": matter.version - 1}).status_code == 409
        assert client.post(endpoint, json={"version": matter.version, "path": written["path"]}).status_code == 422
        assert (
            client.post(
                endpoint.replace(written["artifact_id"], "foreign"), json={"version": matter.version}
            ).status_code
            == 400
        )
        assert not calls
        assert client.post(endpoint, json={"version": matter.version}).json()["opened"]
        assert calls == [(Path(written["path"]), "excel")]
        Path(written["path"]).write_bytes(b"changed")
        matter = store.get(ButlerMatter, matter.id)
        assert client.post(endpoint, json={"version": matter.version}).status_code == 400
        assert len(calls) == 1


def test_saved_workbook_is_verified_in_fresh_backend_process(office_case):
    import json
    import os
    import subprocess
    import sys

    context, args, _ = office_case
    written, _ = saved_workbook(office_case)
    worker = """
import json, sys
from app.core import butler_store as store
from app.core.butler_models import ButlerMatter
from app.services.office_artifacts import verify_artifact
from app.policy.execution_marker import mark_execution_approved
from app.tools import office_document_tools as office
matter = store.get(ButlerMatter, 'matter-office')
assert len(matter.artifacts) == 1
assert verify_artifact(matter, matter.artifacts[0])['整理数据'][1][:3] == ['青山', '林晓', 12]
request, context = json.loads(sys.stdin.read())
office.create_docx(request, context)
mark_execution_approved(context)
assert office.create_docx({**request, 'dry_run': False}, context)['verified']
"""
    request = {
        "path": str(Path(args["path"]).with_suffix(".docx")),
        "source_path": written["path"],
        "summary": "青山项目由林晓负责。",
    }
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONUTF8": "1"}
    completed = subprocess.run(
        [sys.executable, "-c", worker],
        input=json.dumps([request, context]),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert len(list(Path(args["path"]).parent.glob("*.xlsx"))) == 1
    assert len(list(Path(args["path"]).parent.glob("*.docx"))) == 1
    assert len(store.get(ButlerMatter, "matter-office").artifacts) == 2


@pytest.mark.parametrize("modified", [False, True])
def test_office_rollback_only_removes_unchanged_generated_file(office_case, monkeypatch, modified):
    from app.tools import rollback_tools

    context, _, _ = office_case
    written, _ = saved_workbook(office_case)
    path = Path(written["path"])
    trash = []

    def controlled_trash(filename):
        target = Path(filename)
        assert target == path
        target.rename(target.with_suffix(".trashed"))
        trash.append(filename)

    monkeypatch.setattr(rollback_tools, "send2trash", controlled_trash)
    if modified:
        path.write_bytes(b"user changes")
    result = ToolResult(tool_call_id="call-office-rollback", ok=True, rollback_info=written["rollback_info"])
    outcome = rollback_tools.rollback_tool_result(result, context)
    assert outcome["ok"] is not modified
    assert bool(trash) is not modified
    assert path.exists() is modified


def test_privacy_erase_removes_office_records_and_preserves_user_outputs(office_case):
    written, _ = saved_workbook(office_case)
    erased = db.erase_local_user_data()
    assert erased["butler_matters"] == 1
    assert not store.list_records(ButlerMatter)
    assert Path(written["path"]).exists()


def test_planning_continuation_receives_complete_reviewed_page_and_workbook(office_case):
    from app.services.office_artifacts import planning_readbacks

    before = planning_readbacks(store.get(ButlerMatter, "matter-office"))
    assert before["incomplete"] is False
    assert before["observations"][0]["data"]["text"] == TEXT
    written, _ = saved_workbook(office_case)
    after = planning_readbacks(store.get(ButlerMatter, "matter-office"))
    readback = next(item for item in after["observations"] if item["tool"] == "document.read_artifact")
    assert readback["data"]["sha256"] == written["sha256"]
    assert readback["data"]["data"]["整理数据"][1][:3] == ["青山", "林晓", 12]


def test_restored_readback_preserves_taint_without_reusing_old_approval():
    from app.core.content_provenance import (
        content_envelope_integrity_valid,
        create_content_envelope,
        restore_untrusted_content,
        revalidate_content_envelope,
    )

    original = create_content_envelope(
        TEXT,
        source_kind="web",
        task_scope="previous",
        trust_level="user_confirmed",
        user_confirmed=True,
        taint_flags=["external"],
    )
    restored = restore_untrusted_content(original, TEXT, task_scope="current", source_id="stored-call")
    assert content_envelope_integrity_valid(restored)
    assert not restored.user_confirmed and restored.trust_level == "untrusted"
    assert restored.taint_flags == ["external"] and restored.field_lineage
    assert restored.task_scope == "current"
    assert restored == restore_untrusted_content(original, TEXT, task_scope="current", source_id="stored-call")
    assert revalidate_content_envelope(restored, TEXT, task_scope="current").user_confirmed


def test_approved_provenance_never_covers_new_source_data():
    from types import SimpleNamespace

    from app.core.content_provenance import create_content_envelope
    from app.orchestration.handlers.step_execution_handler import StepExecutionHandler

    old = create_content_envelope("reviewed", source_kind="web", task_scope="task-a", trust_level="untrusted")
    new = create_content_envelope("changed", source_kind="web", task_scope="task-a", trust_level="untrusted")
    approval = SimpleNamespace(engineering_boundary={"content_provenance": {"upstream_envelopes": [old.model_dump()]}})
    runtime = SimpleNamespace(extra_context={"upstream_content_envelopes": [new.model_dump()]})
    handler = StepExecutionHandler(None)
    assert handler._bind_approved_content_provenance(runtime, approval, {"path": "report.xlsx"}, "task-a")
    assert "approved_content_envelopes" not in runtime.extra_context
    runtime.extra_context["upstream_content_envelopes"] = [old.model_dump()]
    assert not handler._bind_approved_content_provenance(runtime, approval, {"path": "report.xlsx"}, "task-a")
    assert runtime.extra_context["upstream_content_envelopes"][0]["user_confirmed"]


def test_occupied_office_output_is_not_opened_or_overwritten(office_case):
    import os

    if os.name != "nt":
        pytest.skip("Windows file sharing contract")
    import ctypes
    from ctypes import wintypes

    from app.services.office_artifacts import public_artifacts

    context, args, _ = office_case
    written, _ = saved_workbook(office_case)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(written["path"], 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(OSError):
            office.read_artifact({"path": written["path"]}, context)
        assert not public_artifacts(store.get(ButlerMatter, "matter-office"))[0]["verified"]
        with pytest.raises(OSError):
            office.create_xlsx({**args, "dry_run": False}, context)
    finally:
        kernel.CloseHandle(handle)
    assert office.read_artifact({"path": written["path"]}, context)["sha256"] == written["sha256"]


@pytest.mark.asyncio
async def test_real_runtime_scoped_creation_approval_and_restore(office_case, monkeypatch):
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.core.schemas import Approval, Plan, PlanStep, Run, RunEngine, RunPhase, TaskStatus
    from app.orchestration.orchestrator_registry import orchestrator_registry
    from app.policy.policy_engine import PolicyEngine
    from app.services import run_service
    from app.services.butler_service import ButlerCoordinator

    context, args, _ = office_case
    original_init = PolicyEngine.__init__

    def fixed_clock(self, *positional, **kwargs):
        original_init(self, *positional, **{**kwargs, "now_provider": lambda: datetime(2026, 9, 22, 12, tzinfo=UTC)})

    monkeypatch.setattr(PolicyEngine, "__init__", fixed_clock)
    task = Task.model_validate(db.fetch_one("tasks", context["task_id"]))
    task.status = TaskStatus.REVIEWING_PLAN
    db.upsert_model("tasks", task)
    run = Run(message=task.user_goal, task_id=task.id, engine=RunEngine.OS, phase=RunPhase.AWAITING_APPROVAL)
    db.upsert_model("runs", run)
    store.mutate(ButlerMatter, "matter-office", lambda m: setattr(m, "active_run_id", run.id))
    step = PlanStep(
        task_id=task.id, tool_name="document.create_xlsx", agent_name="DocumentAgent", description="新建表格", args=args
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    orchestrator = OrchestratorAgent()
    orchestrator.planning_handler._annotate_plan_tool_contracts(plan, [t.name for t in orchestrator.registry.list()])
    db.upsert_model("plans", plan)
    await orchestrator._process_steps(task, plan)
    approvals = db.fetch_many("approvals", "task_id=?", (task.id,))
    assert len(approvals) == 1, db.fetch_one("tasks", task.id)
    assert approvals[0]["source"] == "autonomy_scope"
    assert not Path(args["path"]).exists()
    orchestrator_registry.release_task(task.id)
    monkeypatch.setattr(run_service, "_run_active", lambda _: False)
    monkeypatch.setattr(run_service, "resume_runs_for_task", lambda *a, **kw: None)
    try:
        await ButlerCoordinator().sync_run(store.get(ButlerMatter, "matter-office"))
        approval = Approval.model_validate(db.fetch_one("approvals", approvals[0]["id"]))
        assert approval.consumed_at
        assert Path(args["path"]).exists(), db.fetch_one("tasks", task.id)
        verify_artifact(store.get(ButlerMatter, "matter-office"), store.get(ButlerMatter, "matter-office").artifacts[0])
    finally:
        orchestrator_registry.release_task(task.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["xlsx", "docx"])
async def test_single_saved_office_document_completes_without_opening(office_case, kind):
    from app.services.butler_evidence import verify_conditions

    context, args, _ = office_case
    written, _ = saved_workbook(office_case)
    if kind == "docx":
        report_args = {
            "path": str(Path(args["path"]).with_suffix(".docx")),
            "source_path": written["path"],
            "title": "项目汇总",
            "summary": "项目摘要",
        }
        office.create_docx(report_args, context)
        written = office.create_docx({**report_args, "dry_run": False}, context)
        journal(context, "document.read_artifact", office.read_artifact({"path": written["path"]}, context))
    matter = store.get(ButlerMatter, "matter-office")
    artifact = next(item for item in matter.artifacts if item.kind == kind)
    # Exercise a standalone document with independently verified source content.
    artifact.source_artifact_id = ""
    artifact.source_sha256 = ""
    matter.artifacts = [artifact]
    name, other = ("Excel", "Word") if kind == "xlsx" else ("Word", "Excel")
    matter.goal = f"只生成 {name}，不生成 {other}，也不要打开 Office"
    from app.core.butler_models import CompletionCondition

    matter.completion_conditions = [CompletionCondition(kind="file_exists", subject=written["path"])]
    assert not artifact.opened
    valid, evidence, reason = await verify_conditions(matter)
    assert valid, reason
    assert any(item.get("artifact_id") == artifact.id for item in evidence)
    matter.completion_conditions = [CompletionCondition(kind="office_artifacts", artifact_kinds=[kind])]
    assert (await verify_conditions(matter))[0]
    # Explicit no-open intent also overrides an inappropriate planner condition.
    matter.completion_conditions[0].require_open = True
    assert (await verify_conditions(matter))[0]
    Path(artifact.path).write_bytes(b"modified after verification")
    assert not (await verify_conditions(matter))[0]


@pytest.mark.asyncio
async def test_explicit_dual_office_workflow_keeps_both_artifacts_and_open_checks(office_case):
    from app.core.butler_models import CompletionCondition
    from app.services.butler_evidence import verify_conditions

    context, args, _ = office_case
    written, _ = saved_workbook(office_case)
    matter = store.get(ButlerMatter, "matter-office")
    matter.goal = "生成 Excel 和 Word，并打开两份成果"
    matter.completion_conditions = [
        CompletionCondition(kind="office_artifacts", artifact_kinds=["xlsx", "docx"], require_open=True)
    ]
    valid, _, reason = await verify_conditions(matter)
    assert not valid and "Word" in reason
    report_args = {
        "path": str(Path(args["path"]).with_suffix(".docx")),
        "source_path": written["path"],
        "title": "项目汇总",
        "summary": "项目摘要",
    }
    office.create_docx(report_args, context)
    office.create_docx({**report_args, "dry_run": False}, context)
    saved = store.get(ButlerMatter, "matter-office")
    matter.artifacts = saved.artifacts
    valid, _, reason = await verify_conditions(matter)
    assert not valid and "打开" in reason
    for artifact in matter.artifacts:
        artifact.opened = True
    assert (await verify_conditions(matter))[0]
