from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import SecurityError
from app.services.cleanup_planner_service import CleanupPlannerService
from app.tools import file_tools
from app.tools.registry import register_all_tools


def _context(root: Path) -> dict:
    return {"allowed_directories": [str(root)]}


def _selected(plan: dict, action: str) -> list[str]:
    return [item["id"] for item in plan["items"] if item["action"] == action]


def test_cleanup_plan_hash_is_stable_for_same_files(tmp_path: Path):
    cache_dir = tmp_path / ".pytest_cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "node.tmp"
    cache_file.write_text("cache", encoding="utf-8")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    installer = downloads / "old.msi"
    installer.write_bytes(b"0" * 1024)

    service = CleanupPlannerService()
    args = {"roots": [str(tmp_path)], "threshold_mb": 1, "older_than_days": 0}

    first = service.create_plan(args, _context(tmp_path))
    second = service.create_plan(args, _context(tmp_path))

    assert first.content_hash == second.content_hash
    assert first.plan_id == second.plan_id
    assert first.direct_delete_bytes >= cache_file.stat().st_size
    assert any(item.action == "trash_with_prompt" and item.path == str(installer.resolve()) for item in first.items)


def test_cleanup_execute_rejects_tampered_plan_hash(tmp_path: Path):
    cache_dir = tmp_path / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    service = CleanupPlannerService()
    plan = service.create_plan({"roots": [str(tmp_path)]}, _context(tmp_path))

    with pytest.raises(SecurityError, match="plan_id/content_hash"):
        service.execute(
            {
                "roots": [str(tmp_path)],
                "plan_id": plan.plan_id,
                "content_hash": "tampered",
                "selected_item_ids": [plan.items[0].id],
                "dry_run": False,
            },
            _context(tmp_path),
        )
    assert target.exists()


def test_cleanup_execute_allows_only_whitelisted_direct_delete(tmp_path: Path):
    cache_dir = tmp_path / "build"
    cache_dir.mkdir()
    target = cache_dir / "artifact.tmp"
    target.write_text("cache", encoding="utf-8")
    service = CleanupPlannerService()
    plan = service.create_plan({"roots": [str(tmp_path)]}, _context(tmp_path))
    selected = [item.id for item in plan.items if item.path == str(target.resolve())]

    result = service.execute(
        {
            "roots": [str(tmp_path)],
            "plan_id": plan.plan_id,
            "content_hash": plan.content_hash,
            "selected_item_ids": selected,
            "dry_run": False,
        },
        _context(tmp_path),
    )

    assert result["ok"] is True
    assert str(target.resolve()) in result["changed_paths"]
    assert not target.exists()
    assert result["rollback_info"]["permanent_delete_unrecoverable"][0]["path"] == str(target.resolve())


def test_cleanup_execute_trash_requires_approval_and_uses_send2trash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    installer = downloads / "old-installer.zip"
    installer.write_bytes(b"0" * 1024)
    service = CleanupPlannerService()
    args = {"roots": [str(tmp_path)], "older_than_days": 0}
    plan = service.create_plan(args, _context(tmp_path))
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
            _context(tmp_path),
        )

    result = service.execute(
        {
            **args,
            "plan_id": plan.plan_id,
            "content_hash": plan.content_hash,
            "selected_item_ids": selected,
            "dry_run": False,
            "approved": True,
            "approval_id": "approval-1",
        },
        _context(tmp_path),
    )

    assert trashed == [str(installer.resolve())]
    assert result["rollback_info"]["restore_from_recycle_bin"] == [str(installer.resolve())]


def test_cleanup_tools_are_registered_with_schemas(tmp_path: Path):
    registry = register_all_tools(load_skills=False)

    for name in ("file.cleanup_scan", "file.cleanup_plan", "file.cleanup_execute", "file.cleanup_rollback", "file.dedupe_plan"):
        tool = registry.get(name)
        assert tool.input_schema["type"] == "object"

    plan = file_tools.cleanup_plan({"roots": [str(tmp_path)]}, _context(tmp_path))
    assert plan["ok"] is True
    assert plan["plan_id"].startswith("cleanup_")

