from __future__ import annotations

import builtins
import sys
import threading
import types
from pathlib import Path
from typing import Any

import pytest

from app.config import AppSettings
from app.core import db
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools import app_excel
from app.tools.registry import register_all_tools
from app.tools.tool_abort import ToolAbortedError


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
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


def test_excel_open_import_guard_is_narrow(monkeypatch):
    real_import = builtins.__import__

    def import_missing(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "win32com.client":
            raise ImportError("missing pywin32")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_missing)
    with pytest.raises(app_excel.ExcelUnavailableError, match="pywin32 is not installed"):
        app_excel.PyWin32ExcelClient()._open_excel()

    def import_bug(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "win32com.client":
            raise RuntimeError("import hook bug")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_bug)
    with pytest.raises(RuntimeError, match="import hook bug"):
        app_excel.PyWin32ExcelClient()._open_excel()


def test_excel_com_errors_are_wrapped_for_dispatch_but_not_unexpected_import_bugs(monkeypatch):
    class FakeComError(Exception):
        pass

    fake_pywintypes = types.ModuleType("pywintypes")
    fake_pywintypes.error = FakeComError
    fake_pywintypes.com_error = FakeComError
    fake_win32com = types.ModuleType("win32com")
    fake_client = types.ModuleType("win32com.client")
    fake_client.DispatchEx = lambda _name: (_ for _ in ()).throw(FakeComError("excel unavailable"))
    fake_win32com.client = fake_client

    monkeypatch.setitem(sys.modules, "pywintypes", fake_pywintypes)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)

    with pytest.raises(app_excel.ExcelUnavailableError, match="Microsoft Excel COM automation is unavailable"):
        app_excel.PyWin32ExcelClient()._open_excel()


def test_configure_excel_allows_xlsx_when_security_setting_is_unsupported(monkeypatch):
    class FakeComError(Exception):
        pass

    fake_pywintypes = types.ModuleType("pywintypes")
    fake_pywintypes.error = FakeComError
    fake_pywintypes.com_error = FakeComError
    monkeypatch.setitem(sys.modules, "pywintypes", fake_pywintypes)

    class FakeExcel:
        def __setattr__(self, name, value):
            if name == "AutomationSecurity":
                raise FakeComError("security unsupported")
            super().__setattr__(name, value)

    excel = FakeExcel()
    app_excel._configure_excel(excel, visible=False, workbook_path=Path("budget.xlsx"))

    assert excel.Visible is False
    assert excel.DisplayAlerts is False


@pytest.mark.parametrize("suffix", [".xls", ".xlsb", ".xlsm"])
@pytest.mark.parametrize("operation", ["read", "write"])
def test_excel_client_blocks_macro_formats_before_open_when_security_setting_fails(monkeypatch, suffix, operation):
    class FakeComError(Exception):
        pass

    fake_pywintypes = types.ModuleType("pywintypes")
    fake_pywintypes.error = FakeComError
    fake_pywintypes.com_error = FakeComError
    monkeypatch.setitem(sys.modules, "pywintypes", fake_pywintypes)

    class FakeWorkbooks:
        def __init__(self):
            self.open_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def Open(self, *args, **kwargs):  # noqa: N802 - mirrors the Excel COM API
            self.open_calls.append((args, kwargs))
            raise AssertionError("Workbooks.Open must not run without confirmed macro security")

    class FakeExcel:
        def __init__(self):
            self.Workbooks = FakeWorkbooks()
            self.quit_called = False

        def __setattr__(self, name, value):
            if name == "AutomationSecurity":
                raise FakeComError("security unsupported")
            super().__setattr__(name, value)

        def Quit(self):  # noqa: N802 - mirrors the Excel COM API
            self.quit_called = True

    excel = FakeExcel()
    monkeypatch.setattr(app_excel.PyWin32ExcelClient, "_open_excel", lambda _self: excel)
    client = app_excel.PyWin32ExcelClient()
    path = Path(f"budget{suffix}")

    with pytest.raises(app_excel.ExcelUnavailableError, match="not supported"):
        if operation == "read":
            client.read_workbook_summary(path, max_rows=1, max_columns=1)
        else:
            client.write_cell(path, sheet="Sheet1", cell="A1", value="safe")

    assert excel.Workbooks.open_calls == []
    assert excel.quit_called is True


def test_configure_excel_blocks_macro_format_when_security_readback_is_not_force_disable():
    class FakeExcel:
        AutomationSecurity = 1

        def __setattr__(self, name, value):
            if name == "AutomationSecurity":
                value = 1
            super().__setattr__(name, value)

    with pytest.raises(app_excel.ExcelUnavailableError, match="not supported"):
        app_excel._configure_excel(FakeExcel(), visible=False, workbook_path=Path("budget.xlsm"))


def test_excel_client_rejects_macro_format_even_when_security_is_set(monkeypatch):
    class FakeWorksheets:
        Count = 0

    class FakeWorkbook:
        def __init__(self):
            self.Worksheets = FakeWorksheets()
            self.closed = False

        def Close(self, *, SaveChanges):  # noqa: N802, N803 - mirrors the Excel COM API
            assert SaveChanges is False
            self.closed = True

    class FakeWorkbooks:
        def __init__(self, workbook):
            self.workbook = workbook
            self.open_calls: list[tuple[str, dict[str, Any]]] = []

        def Open(self, path, **kwargs):  # noqa: N802 - mirrors the Excel COM API
            self.open_calls.append((path, kwargs))
            return self.workbook

    class FakeExcel:
        def __init__(self, workbook):
            self.AutomationSecurity = 1
            self.Workbooks = FakeWorkbooks(workbook)
            self.quit_called = False

        def Quit(self):  # noqa: N802 - mirrors the Excel COM API
            self.quit_called = True

    workbook = FakeWorkbook()
    excel = FakeExcel(workbook)
    monkeypatch.setattr(app_excel.PyWin32ExcelClient, "_open_excel", lambda _self: excel)

    with pytest.raises(app_excel.ExcelUnavailableError, match="not supported"):
        app_excel.PyWin32ExcelClient().read_workbook_summary(Path("budget.xlsm"), max_rows=1, max_columns=1)

    assert excel.Workbooks.open_calls == []
    assert workbook.closed is False
    assert excel.quit_called is True


def test_resolve_workbook_path_rejects_legacy_and_macro_formats(tmp_path):
    for suffix in (".xls", ".xlsb", ".xlsm"):
        path = tmp_path / f"budget{suffix}"
        path.write_text("not opened", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported Excel workbook extension"):
            app_excel._resolve_workbook_path({"path": str(path)}, {"allowed_directories": [str(tmp_path)]})


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


def test_write_cell_aborts_before_excel_client_write(tmp_path):
    workbook = _workbook(tmp_path)
    client = FakeExcelClient()
    abort = threading.Event()
    abort.set()

    with pytest.raises(ToolAbortedError):
        app_excel.write_cell(
            {
                "path": str(workbook),
                "sheet": "Sheet1",
                "cell": "C3",
                "value": 123,
                "dry_run": False,
                "approved": True,
            },
            {**_context(tmp_path, client), "_tool_abort_event": abort},
        )

    assert client.writes == []


def test_write_cell_rejects_non_allowlisted_formula(tmp_path):
    workbook = _workbook(tmp_path)

    with pytest.raises(ValueError, match="Formula writes"):
        app_excel.write_cell(
            {"path": str(workbook), "sheet": "Sheet1", "cell": "A1", "value": '=HYPERLINK("https://example.com")'},
            _context(tmp_path),
        )


@pytest.mark.parametrize("value", ["+SUM(1)", "-2+3", "@HYPERLINK(1)", "  =cmd|' /C calc'!A0", "\t=1+1"])
def test_write_cell_rejects_all_formula_trigger_prefixes(tmp_path, value):
    workbook = _workbook(tmp_path)

    with pytest.raises(ValueError, match="Formula writes"):
        app_excel.write_cell(
            {"path": str(workbook), "sheet": "Sheet1", "cell": "A1", "value": value},
            _context(tmp_path),
        )


@pytest.mark.parametrize("value", ["-5", "-5.5", "safe text", "42"])
def test_write_cell_allows_plain_text_and_negative_numbers(tmp_path, value):
    from app.tools.app_excel import _validate_cell_value

    # Must not raise: a leading - followed by a plain number is a numeric
    # literal, not a formula.
    _validate_cell_value(value)


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
    assert write_tool.input_schema["type"] == "object"
    assert write_tool.input_schema["required"] == ["path", "sheet", "cell", "value"]
    assert set(write_tool.input_schema["required"]) <= set(write_tool.input_schema["properties"])


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
