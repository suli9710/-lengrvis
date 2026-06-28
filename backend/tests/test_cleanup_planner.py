from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from app.api import routes_files
from app.config import AppSettings
from app.core import db
from app.core.errors import SecurityError
from app.core.schemas import Approval, ApprovalStatus
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel
from app.services.cleanup_planner_service import CleanupPlannerService
from app.tools import file_tools
from app.tools.registry import register_all_tools
from app.tools.tool_abort import ToolAbortedError


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / ".db"))
    db.init_db()
    yield


def _context(root: Path) -> dict:
    return {"allowed_directories": [str(root)]}


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def _selected(plan: dict, action: str) -> list[str]:
    return [item["id"] for item in plan["items"] if item["action"] == action]


def test_cleanup_plan_hash_is_stable_for_same_files(tmp_path: Path):
    root = _workspace(tmp_path)
    cache_dir = root / ".pytest_cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "node.tmp"
    cache_file.write_text("cache", encoding="utf-8")
    downloads = root / "Downloads"
    downloads.mkdir()
    installer = downloads / "old.msi"
    installer.write_bytes(b"0" * 1024)

    service = CleanupPlannerService()
    args = {"roots": [str(root)], "threshold_mb": 1, "older_than_days": 0}

    first = service.create_plan(args, _context(root))
    second = service.create_plan(args, _context(root))

    assert first.content_hash == second.content_hash
    assert first.plan_id == second.plan_id
    assert first.direct_delete_bytes >= cache_file.stat().st_size
    assert any(item.action == "trash_with_prompt" and item.path == str(installer.resolve()) for item in first.items)


def test_cleanup_execute_rejects_tampered_plan_hash(tmp_path: Path):
    root = _workspace(tmp_path)
    cache_dir = root / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    service = CleanupPlannerService()
    plan = service.create_plan({"roots": [str(root)]}, _context(root))

    with pytest.raises(SecurityError, match="plan_id/content_hash"):
        service.execute(
            {
                "roots": [str(root)],
                "plan_id": plan.plan_id,
                "content_hash": "tampered",
                "selected_item_ids": [plan.items[0].id],
                "dry_run": False,
            },
            _context(root),
        )
    assert target.exists()


def test_cleanup_execute_direct_delete_requires_valid_approval(tmp_path: Path):
    root = _workspace(tmp_path)
    cache_dir = root / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    service = CleanupPlannerService()
    args = {"roots": [str(root)]}
    context = _context(root)
    plan = service.create_plan(args, context)
    selected = [item.id for item in plan.items if item.path == str(target.resolve())]
    execute_args = {
        **args,
        "plan_id": plan.plan_id,
        "content_hash": plan.content_hash,
        "selected_item_ids": selected,
        "dry_run": False,
    }

    with pytest.raises(SecurityError, match="approval_id"):
        service.execute(execute_args, context)

    preview = service.execute({**execute_args, "dry_run": True}, context)
    approval = _approved_cleanup_execution(execute_args, context, preview)
    execute_args = {**execute_args, "approved": True, "approval_id": approval.id}

    result = service.execute(
        execute_args,
        context,
    )

    assert result["ok"] is True
    assert str(target.resolve()) in result["changed_paths"]
    assert not target.exists()
    assert result["rollback_info"]["permanent_delete_unrecoverable"][0]["path"] == str(target.resolve())


def test_cleanup_execute_aborts_before_direct_delete(tmp_path: Path):
    root = _workspace(tmp_path)
    cache_dir = root / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    service = CleanupPlannerService()
    args = {"roots": [str(root)]}
    context = _context(root)
    plan = service.create_plan(args, context)
    selected = [item.id for item in plan.items if item.path == str(target.resolve())]
    execute_args = {
        **args,
        "plan_id": plan.plan_id,
        "content_hash": plan.content_hash,
        "selected_item_ids": selected,
        "dry_run": False,
    }
    preview = service.execute({**execute_args, "dry_run": True}, context)
    approval = _approved_cleanup_execution(execute_args, context, preview)
    abort = threading.Event()
    abort.set()

    with pytest.raises(ToolAbortedError):
        service.execute(
            {**execute_args, "approved": True, "approval_id": approval.id},
            {**context, "_tool_abort_event": abort},
        )

    assert target.exists()


def _approved_cleanup_execution(args: dict, context: dict, preview: dict) -> Approval:
    approval = Approval(
        task_id="direct_cleanup_api",
        step_id=None,
        message="Approve cleanup execution",
        status=ApprovalStatus.APPROVED,
        tool_name="file.cleanup_execute",
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value,
        args_binding_hmac=args_binding_hmac("file.cleanup_execute", args, task_id="direct_cleanup_api", step_id=None),
        preview_hmac=preview_hmac(preview),
        settings_fingerprint=settings_fingerprint(
            context.get("settings"), allowed_directories=context.get("allowed_directories") or []
        ),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version="1",
        diff_preview=preview,
    )
    db.upsert_model("approvals", approval, status=approval.status)
    return approval


def test_cleanup_execute_trash_requires_valid_approval_and_uses_send2trash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    db.init_db()
    root = _workspace(tmp_path)
    downloads = root / "Downloads"
    downloads.mkdir()
    installer = downloads / "old-installer.zip"
    installer.write_bytes(b"0" * 1024)
    service = CleanupPlannerService()
    args = {"roots": [str(root)], "older_than_days": 0}
    context = _context(root)
    plan = service.create_plan(args, context)
    selected = [item.id for item in plan.items if item.path == str(installer.resolve())]
    trashed: list[str] = []

    def fake_send2trash(path: str) -> None:
        trashed.append(path)
        Path(path).unlink()

    monkeypatch.setattr("app.services.cleanup_planner_service.send2trash", fake_send2trash)

    with pytest.raises(SecurityError, match="approval_id"):
        service.execute(
            {
                **args,
                "plan_id": plan.plan_id,
                "content_hash": plan.content_hash,
                "selected_item_ids": selected,
                "dry_run": False,
            },
            context,
        )

    forged_args = {
        **args,
        "plan_id": plan.plan_id,
        "content_hash": plan.content_hash,
        "selected_item_ids": selected,
        "dry_run": False,
        "approved": True,
        "approval_id": "approval-1",
    }
    with pytest.raises(SecurityError, match="approval database"):
        service.execute(forged_args, context)

    approved_args = {**forged_args, "approval_id": ""}
    preview = service.execute(
        {
            **args,
            "plan_id": plan.plan_id,
            "content_hash": plan.content_hash,
            "selected_item_ids": selected,
            "dry_run": True,
        },
        context,
    )
    approval = _approved_cleanup_execution(approved_args, context, preview)
    approved_args["approval_id"] = approval.id

    result = service.execute(
        approved_args,
        context,
    )

    assert trashed == [str(installer.resolve())]
    assert result["rollback_info"]["restore_from_recycle_bin"] == [str(installer.resolve())]
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed.consumed_at


def test_cleanup_execute_revalidates_approval_after_claim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = _workspace(tmp_path)
    cache_dir = root / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    service = CleanupPlannerService()
    args = {"roots": [str(root)]}
    context = _context(root)
    plan = service.create_plan(args, context)
    selected = [item.id for item in plan.items if item.path == str(target.resolve())]
    execute_args = {
        **args,
        "plan_id": plan.plan_id,
        "content_hash": plan.content_hash,
        "selected_item_ids": selected,
        "dry_run": False,
    }
    preview = service.execute({**execute_args, "dry_run": True}, context)
    approval = _approved_cleanup_execution(execute_args, context, preview)
    execute_args = {**execute_args, "approved": True, "approval_id": approval.id}
    original_claim = db.claim_approval_for_execution

    def claim_and_tamper(approval_id: str, consumed_at: str):
        claimed = original_claim(approval_id, consumed_at)
        if claimed:
            claimed["tool_name"] = "file.trash"
        return claimed

    monkeypatch.setattr("app.services.cleanup_planner_service.db.claim_approval_for_execution", claim_and_tamper)

    with pytest.raises(SecurityError, match="tool name"):
        service.execute(execute_args, context)

    assert target.exists()
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed.consumed_at


def test_cleanup_execute_rejects_file_replaced_after_preview(tmp_path: Path):
    root = _workspace(tmp_path)
    cache_dir = root / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    service = CleanupPlannerService()
    args = {"roots": [str(root)]}
    context = _context(root)
    plan = service.create_plan(args, context)
    selected = [item.id for item in plan.items if item.path == str(target.resolve())]
    execute_args = {
        **args,
        "plan_id": plan.plan_id,
        "content_hash": plan.content_hash,
        "selected_item_ids": selected,
        "dry_run": False,
    }
    preview = service.execute({**execute_args, "dry_run": True}, context)
    approval = _approved_cleanup_execution(execute_args, context, preview)

    target.write_text("owned!", encoding="utf-8")

    with pytest.raises(SecurityError, match="plan_id/content_hash|file identity changed"):
        service.execute({**execute_args, "approved": True, "approval_id": approval.id}, context)

    assert target.read_text(encoding="utf-8") == "owned!"


def test_cleanup_execute_rejects_same_size_same_mtime_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    root = _workspace(tmp_path)
    cache_dir = root / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    service = CleanupPlannerService()
    args = {"roots": [str(root)]}
    context = _context(root)
    plan = service.create_plan(args, context)
    item = next(item for item in plan.items if item.path == str(target.resolve()))
    execute_args = {
        **args,
        "plan_id": plan.plan_id,
        "content_hash": plan.content_hash,
        "selected_item_ids": [item.id],
        "dry_run": False,
    }
    preview = service.execute({**execute_args, "dry_run": True}, context)
    approval = _approved_cleanup_execution(execute_args, context, preview)

    original_claim = db.claim_approval_for_execution

    def claim_then_replace(approval_id: str, consumed_at: str):
        claimed = original_claim(approval_id, consumed_at)
        target.unlink()
        target.write_text("owned", encoding="utf-8")
        os.utime(target, ns=(item.mtime_ns, item.mtime_ns))
        return claimed

    monkeypatch.setattr("app.services.cleanup_planner_service.db.claim_approval_for_execution", claim_then_replace)

    with pytest.raises(SecurityError, match="file identity changed"):
        service.execute({**execute_args, "approved": True, "approval_id": approval.id}, context)

    assert target.read_text(encoding="utf-8") == "owned"


def test_cleanup_execute_route_requires_policy_and_bound_approval(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = _workspace(tmp_path)
    cache_dir = root / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    settings = AppSettings(provider_name="mock", mode="efficiency", allowed_directories=[str(root)])
    monkeypatch.setattr(routes_files, "get_effective_settings", lambda: settings)
    service = CleanupPlannerService()
    args = {"roots": [str(root)]}
    context = {"allowed_directories": [str(root)], "settings": settings}
    plan = service.create_plan(args, context)
    selected = [item.id for item in plan.items if item.path == str(target.resolve())]
    payload = {
        **args,
        "plan_id": plan.plan_id,
        "content_hash": plan.content_hash,
        "selected_item_ids": selected,
        "dry_run": False,
    }

    blocked = routes_files.cleanup_execute(payload)
    assert blocked["status"] in {"requires_approval", "denied"}
    assert target.exists()

    forged = routes_files.cleanup_execute({**payload, "approved": True, "approval_id": "approval-forged"})
    assert forged["status"] == "denied"
    assert "approval" in forged["error"].lower()
    assert target.exists()

    preview = service.execute({**payload, "dry_run": True}, context)
    approval = _approved_cleanup_execution(payload, context, preview)
    blocked_valid = routes_files.cleanup_execute({**payload, "approved": True, "approval_id": approval.id})

    assert blocked_valid["status"] == "denied"
    assert "direct file api cannot consume approval" in blocked_valid["error"].lower()
    assert target.exists()
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed.consumed_at is None


def test_cleanup_rollback_route_blocks_direct_live_execution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = _workspace(tmp_path)
    settings = AppSettings(provider_name="mock", mode="efficiency", allowed_directories=[str(root)])
    monkeypatch.setattr(routes_files, "get_effective_settings", lambda: settings)
    payload = {
        "rollback_info": {"restore_from_recycle_bin": str(root / "trashed.txt")},
        "dry_run": False,
    }

    blocked = routes_files.cleanup_rollback(payload)
    assert blocked["status"] in {"requires_approval", "denied"}

    forged = routes_files.cleanup_rollback({**payload, "approved": True, "approval_id": "approval-forged"})
    assert forged["status"] == "denied"
    assert "direct file api cannot consume approval" in forged["error"].lower()


def test_cleanup_tools_are_registered_with_schemas(tmp_path: Path):
    registry = register_all_tools(load_skills=False)

    for name in (
        "file.cleanup_scan",
        "file.cleanup_plan",
        "file.cleanup_execute",
        "file.cleanup_rollback",
        "file.dedupe_plan",
    ):
        tool = registry.get(name)
        assert tool.input_schema["type"] == "object"

    plan = file_tools.cleanup_plan({"roots": [str(tmp_path)]}, _context(tmp_path))
    assert plan["ok"] is True
    assert plan["plan_id"].startswith("cleanup_")
