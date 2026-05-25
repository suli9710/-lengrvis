from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import AppSettings
from app.core import db
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools import app_excel
from app.tools.registry import register_all_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


class FakeExcelClient:
    mode = "mock"

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        return {"available": True, "mode": self.mode, "version": "mock-1.0"}

    def read_workbook_summary(self, path: Path, *, max_rows: int, max_columns: int) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "workbook": str(path),
            "limits": {"max_rows": max_rows, "max_columns": max_columns},
            "sheets": [
                {
                    "name": "Sheet1",
                    "used_range": {"rows": 2, "columns": 2},
                    "preview": [["name", "amount"], ["Ada", 42]],
                }
            ],
        }

    def write_cell(self, path: Path, *, sheet: str, cell: str, value: Any) -> dict[str, Any]:
        payload = {"path": str(path), "sheet": sheet, "cell": cell, "value": value}
        self.writes.append(payload)
        return {
            "mode": self.mode,
            "workbook": str(path),
            "sheet": sheet,
            "cell": cell,
            "previous_value": "old",
            "new_value": value,
        }


def _context(workspace: Path, client: Any | None = None) -> dict[str, Any]:
    settings = AppSettings(allowed_directories=[str(workspace)], provider_name="mock")
    context: dict[str, Any] = {"settings": settings, "allowed_directories": settings.allowed_directories}
    if client is not None:
        context["excel_client"] = client
    return context


def _workbook(workspace: Path) -> Path:
    path = workspace / "budget.xlsx"
    path.write_text("mock workbook", encoding="utf-8")
    return path


def test_excel_status_reports_unavailable_without_com(monkeypatch):
    def _raise_unavailable():
        raise app_excel.ExcelUnavailableError("Excel is not installed")

    monkeypatch.setattr(app_excel, "_client", lambda _context: _raise_unavailable())

    result = app_excel.status({}, {})

    assert result["ok"] is False
    assert result["available"] is False
    assert "write_cell" in result["allowed_operations"]
    assert "not installed" in result["error"]


def test_excel_status_uses_mock_client_when_provided(tmp_path):
    result = app_excel.status({}, _context(tmp_path, FakeExcelClient()))

    assert result["ok"] is True
    assert result["available"] is True
    assert result["mode"] == "mock"
    assert result["allowed_operations"] == ["read_workbook_summary", "status", "write_cell"]


def test_read_workbook_summary_uses_mock_client(tmp_path):
    workbook = _workbook(tmp_path)
    result = app_excel.read_workbook_summary(
        {"path": str(workbook), "max_rows": 1, "max_columns": 1},
        _context(tmp_path, FakeExcelClient()),
    )

    assert result["ok"] is True
    assert result["mode"] == "mock"
    assert result["sheets"][0]["preview"][0] == ["name", "amount"]


def test_write_cell_dry_run_returns_approval_preview_without_excel(tmp_path):
    workbook = _workbook(tmp_path)
    result = app_excel.write_cell(
        {"path": str(workbook), "sheet": "Sheet1", "cell": "b2", "value": "approved value", "dry_run": True},
        _context(tmp_path),
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["diff_preview"] == [
        {
            "action": "write_cell",
            "path": str(workbook.resolve()),
            "sheet": "Sheet1",
            "cell": "B2",
            "new_value": "approved value",
        }
    ]


def test_write_cell_executes_against_mock_client_after_approval(tmp_path):
    workbook = _workbook(tmp_path)
    client = FakeExcelClient()
    result = app_excel.write_cell(
        {
            "path": str(workbook),
            "sheet": "Sheet1",
            "cell": "C3",
            "value": 123,
            "dry_run": False,
            "approved": True,
        },
        _context(tmp_path, client),
    )

    assert result["ok"] is True
    assert result["changed_paths"] == [str(workbook.resolve())]
    assert result["rollback_info"]["previous_value"] == "old"
    assert client.writes == [{"path": str(workbook.resolve()), "sheet": "Sheet1", "cell": "C3", "value": 123}]


def test_write_cell_rejects_non_allowlisted_formula(tmp_path):
    workbook = _workbook(tmp_path)

    with pytest.raises(ValueError, match="Formula writes"):
        app_excel.write_cell(
            {"path": str(workbook), "sheet": "Sheet1", "cell": "A1", "value": "=HYPERLINK(\"https://example.com\")"},
            _context(tmp_path),
        )


def test_excel_tools_are_registered_with_risk_levels(tmp_path):
    registry = register_all_tools(settings=AppSettings(allowed_directories=[str(tmp_path)]), load_skills=False)

    assert registry.get("app.excel.status").risk_level == RiskLevel.R0_READ_ONLY
    assert registry.get("app.excel.status").requires_authorized_path is False
    assert registry.get("app.excel.read_workbook_summary").risk_level == RiskLevel.R0_READ_ONLY
    assert registry.get("app.excel.read_workbook_summary").requires_authorized_path is True
    write_tool = registry.get("app.excel.write_cell")
    assert write_tool.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
    assert write_tool.supports_dry_run is True
    assert write_tool.requires_authorized_path is True


def test_policy_classifies_excel_tools_and_requires_approval_for_write():
    policy = PolicyEngine()

    assert policy.classify_tool_name("app.excel.status") == RiskLevel.R0_READ_ONLY
    assert policy.classify_tool_name("app.excel.read_workbook_summary") == RiskLevel.R0_READ_ONLY
    assert policy.classify_tool_name("app.excel.write_cell") == RiskLevel.R2_REVERSIBLE_MODIFY
    assert policy.classify_tool_name("app.excel.run_macro") == RiskLevel.R4_FORBIDDEN_OR_HANDOFF

    review = policy.review_tool_call(
        "task_excel",
        "step_excel",
        "app.excel.write_cell",
        {"path": "budget.xlsx", "sheet": "Sheet1", "cell": "A1", "value": "ok", "dry_run": True},
        RiskLevel.R2_REVERSIBLE_MODIFY,
    )

    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL
