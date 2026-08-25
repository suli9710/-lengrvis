from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from app.core.audit import record
from app.core.paths import resolve_authorized
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition
from app.tools.tool_abort import raise_if_tool_aborted
from app.tools.tool_catalog import tool_description, tool_search_hint

ALLOWED_OPERATIONS = frozenset(
    {
        "status",
        "read_workbook_summary",
        "write_cell",
    }
)
EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xlsb", ".xls"}
EXCEL_EXTENSIONS_REQUIRING_MACRO_SECURITY = frozenset(EXCEL_EXTENSIONS - {".xlsx"})
# Excel 4.0/XLM macros are not covered by AutomationSecurity, and legacy
# formats can prompt or execute code before the COM caller can establish a
# safe state.  Keep the broader set for diagnostics/error messages, but only
# accept the macro-free OOXML format in the production COM path.
EXCEL_EXTENSIONS_SUPPORTED = frozenset({".xlsx"})
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3
MAX_CELL_TEXT_LENGTH = 32767
MAX_EXCEL_ROW = 1_048_576
MAX_EXCEL_COLUMN = 16_384
logger = logging.getLogger(__name__)

_COM_OPERATION_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


def _pywin32_error_types() -> tuple[type[BaseException], ...]:
    try:
        import pywintypes  # type: ignore[import-not-found]
    except ImportError:
        return _COM_OPERATION_ERRORS
    errors = tuple(
        error_type
        for error_type in (getattr(pywintypes, "error", None), getattr(pywintypes, "com_error", None))
        if isinstance(error_type, type) and issubclass(error_type, BaseException)
    )
    return (*_COM_OPERATION_ERRORS, *errors)


CELL_REF_RE = re.compile(r"^(?P<column>[A-Z]{1,3})(?P<row>[1-9][0-9]{0,6})$")


class ExcelUnavailableError(RuntimeError):
    """Raised when Excel COM automation is unavailable on this host."""


class ExcelClient(Protocol):
    mode: str

    def status(self) -> dict[str, Any]: ...

    def read_workbook_summary(self, path: Path, *, max_rows: int, max_columns: int) -> dict[str, Any]: ...

    def write_cell(self, path: Path, *, sheet: str, cell: str, value: Any) -> dict[str, Any]: ...


@dataclass(slots=True)
class PyWin32ExcelClient:
    visible: bool = False
    mode: str = "com"

    def status(self) -> dict[str, Any]:
        excel = self._open_excel()
        try:
            return {
                "available": True,
                "mode": self.mode,
                "version": str(getattr(excel, "Version", "")),
            }
        finally:
            _quit_excel(excel)

    def read_workbook_summary(self, path: Path, *, max_rows: int, max_columns: int) -> dict[str, Any]:
        excel = self._open_excel()
        workbook = None
        try:
            _configure_excel(excel, visible=self.visible, workbook_path=path)
            workbook = excel.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
            sheets = []
            for index in range(1, int(workbook.Worksheets.Count) + 1):
                worksheet = workbook.Worksheets(index)
                used_range = worksheet.UsedRange
                row_count = int(getattr(used_range.Rows, "Count", 0) or 0)
                column_count = int(getattr(used_range.Columns, "Count", 0) or 0)
                preview_rows = min(row_count, max_rows)
                preview_columns = min(column_count, max_columns)
                preview = []
                for row in range(1, preview_rows + 1):
                    preview.append(
                        [
                            _jsonable_value(worksheet.Cells(row, column).Value)
                            for column in range(1, preview_columns + 1)
                        ]
                    )
                sheets.append(
                    {
                        "name": str(worksheet.Name),
                        "used_range": {"rows": row_count, "columns": column_count},
                        "preview": preview,
                    }
                )
            return {"workbook": str(path), "sheets": sheets}
        finally:
            _close_workbook(workbook, save_changes=False)
            _quit_excel(excel)

    def write_cell(self, path: Path, *, sheet: str, cell: str, value: Any) -> dict[str, Any]:
        excel = self._open_excel()
        workbook = None
        try:
            _configure_excel(excel, visible=self.visible, workbook_path=path)
            workbook = excel.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=False)
            worksheet = workbook.Worksheets(sheet)
            target = worksheet.Range(cell)
            previous_value = _jsonable_value(target.Value)
            target.Value = value
            workbook.Save()
            return {
                "workbook": str(path),
                "sheet": sheet,
                "cell": cell,
                "previous_value": previous_value,
                "new_value": _jsonable_value(value),
            }
        finally:
            _close_workbook(workbook, save_changes=False)
            _quit_excel(excel)

    def _open_excel(self) -> Any:
        try:
            import win32com.client  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on host package
            raise ExcelUnavailableError("pywin32 is not installed; Excel COM automation is unavailable.") from exc

        try:
            return win32com.client.DispatchEx("Excel.Application")
        except _pywin32_error_types() as exc:  # pragma: no cover - depends on installed Excel
            raise ExcelUnavailableError("Microsoft Excel COM automation is unavailable on this host.") from exc


def status(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    try:
        client = _client(context)
        client_status = client.status()
        return {
            "ok": True,
            "available": bool(client_status.get("available", True)),
            "mode": client_status.get("mode", getattr(client, "mode", "unknown")),
            "allowed_operations": sorted(ALLOWED_OPERATIONS),
            **client_status,
        }
    except ExcelUnavailableError as exc:
        return _unavailable(str(exc))


def read_workbook_summary(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_workbook_path(args, context)
    max_rows = _bounded_int(args.get("max_rows"), default=10, minimum=1, maximum=50)
    max_columns = _bounded_int(args.get("max_columns"), default=12, minimum=1, maximum=50)
    try:
        result = _client(context).read_workbook_summary(path, max_rows=max_rows, max_columns=max_columns)
        return {"ok": True, "operation": "read_workbook_summary", "mode": result.get("mode", "com"), **result}
    except ExcelUnavailableError as exc:
        return _unavailable(str(exc))


def write_cell(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_workbook_path(args, context)
    sheet = _validate_sheet_name(args.get("sheet", "Sheet1"))
    cell = _validate_cell_ref(args.get("cell", "A1"))
    value = _validate_cell_value(args.get("value", ""))
    if args.get("dry_run", True):
        return {
            "ok": True,
            "dry_run": True,
            "operation": "write_cell",
            "workbook": str(path),
            "diff_preview": [
                {
                    "action": "write_cell",
                    "path": str(path),
                    "sheet": sheet,
                    "cell": cell,
                    "new_value": _jsonable_value(value),
                }
            ],
            "message": "Approval is required before writing to the workbook through Excel COM.",
        }

    try:
        raise_if_tool_aborted(context)
        result = _client(context).write_cell(path, sheet=sheet, cell=cell, value=value)
    except ExcelUnavailableError as exc:
        return _unavailable(str(exc))

    record(
        "app.excel.write_cell",
        "AppAgent",
        {"path": str(path), "sheet": sheet, "cell": cell},
    )
    return {
        "ok": True,
        "operation": "write_cell",
        "mode": result.get("mode", "com"),
        "changed_paths": [str(path)],
        "rollback_info": {
            "operation": "app.excel.write_cell",
            "path": str(path),
            "sheet": sheet,
            "cell": cell,
            "previous_value": result.get("previous_value"),
        },
        **result,
    }


def _client(context: dict[str, Any]) -> ExcelClient:
    client = context.get("excel_client")
    if client is not None:
        return client
    factory = context.get("excel_client_factory")
    if factory is not None:
        return factory()
    return PyWin32ExcelClient()


def _resolve_workbook_path(args: dict[str, Any], context: dict[str, Any]) -> Path:
    allowed = list(context.get("allowed_directories") or [])
    path = resolve_authorized(str(args.get("path", "")), allowed)
    if path.suffix.lower() not in EXCEL_EXTENSIONS_SUPPORTED:
        raise ValueError(f"Unsupported Excel workbook extension: {path.suffix}")
    if not path.exists():
        raise FileNotFoundError(f"Workbook was not found: {path}")
    if not path.is_file():
        raise ValueError("Workbook path must be a file.")
    return path


def _validate_sheet_name(value: Any) -> str:
    sheet = str(value or "").strip()
    if not sheet:
        raise ValueError("Sheet name is required.")
    if len(sheet) > 31 or any(char in sheet for char in "[]:*?/\\"):
        raise ValueError("Sheet name is not a valid Excel worksheet name.")
    return sheet


def _validate_cell_ref(value: Any) -> str:
    cell = str(value or "").strip().upper()
    match = CELL_REF_RE.match(cell)
    if not match:
        raise ValueError("Cell reference must be an A1-style address.")
    column_index = _column_to_index(match.group("column"))
    row_index = int(match.group("row"))
    if column_index > MAX_EXCEL_COLUMN or row_index > MAX_EXCEL_ROW:
        raise ValueError("Cell reference is outside Excel worksheet bounds.")
    return cell


def _validate_cell_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value)
    if len(text) > MAX_CELL_TEXT_LENGTH:
        raise ValueError("Excel cell text exceeds the maximum supported length.")
    # Excel coerces strings that begin with =, +, - or @ into formulas (and @
    # is rewritten to =), so guarding only "=" leaves the other formula-trigger
    # prefixes open. Leading whitespace/control chars can prefix the trigger, so
    # strip them before checking. A leading +/- followed by a plain number is a
    # harmless numeric literal and is allowed.
    stripped = text.lstrip("\t\r\n \x00")
    lead = stripped[:1]
    if lead in {"=", "@"}:
        raise ValueError("Formula writes are not allowlisted for Excel COM automation.")
    if lead in {"+", "-"} and not _is_plain_number(stripped):
        raise ValueError("Formula writes are not allowlisted for Excel COM automation.")
    return text


def _is_plain_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _column_to_index(column: str) -> int:
    value = 0
    for char in column:
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _configure_excel(excel: Any, *, visible: bool, workbook_path: Path) -> None:
    suffix = workbook_path.suffix.lower()
    if suffix in EXCEL_EXTENSIONS_REQUIRING_MACRO_SECURITY:
        raise ExcelUnavailableError(
            "Legacy or macro-enabled Excel workbooks are not supported by COM automation; use .xlsx instead."
        )
    excel.Visible = visible
    excel.DisplayAlerts = False
    automation_security_confirmed = False
    try:
        excel.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        automation_security_confirmed = int(excel.AutomationSecurity) == MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    except _pywin32_error_types() as exc:
        logger.debug("could not set Excel automation security: %s", exc, exc_info=True)
    if automation_security_confirmed:
        return


def _close_workbook(workbook: Any, *, save_changes: bool) -> None:
    if workbook is None:
        return
    try:
        workbook.Close(SaveChanges=save_changes)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: COM shutdown is best-effort after operation completion.
        logger.debug("could not close Excel workbook cleanly: %s", exc, exc_info=True)


def _quit_excel(excel: Any) -> None:
    try:
        excel.Quit()
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: COM shutdown is best-effort after operation completion.
        logger.debug("could not quit Excel cleanly: %s", exc, exc_info=True)


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "mode": "unavailable",
        "allowed_operations": sorted(ALLOWED_OPERATIONS),
        "error": reason,
    }


def _input_schema(name: str) -> dict[str, Any]:
    if name != "app.excel.write_cell":
        return {}
    return {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Authorized path to the Excel workbook.",
            },
            "sheet": {"type": "string", "description": "Worksheet name."},
            "cell": {
                "type": "string",
                "description": "Cell reference such as B2.",
            },
            "value": {"description": "New cell value."},
            "dry_run": {
                "type": "boolean",
                "description": "Preview the cell change without writing (default true).",
            },
        },
        "required": ["path", "sheet", "cell", "value"],
    }


def register(registry) -> None:
    defs = [
        ("app.excel.status", status, RiskLevel.R0_READ_ONLY, False, False),
        ("app.excel.read_workbook_summary", read_workbook_summary, RiskLevel.R0_READ_ONLY, False, True),
        ("app.excel.write_cell", write_cell, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
    ]
    for name, fn, risk, dry_run, requires_path in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=tool_description(name),
                search_hint=tool_search_hint(name),
                input_schema=_input_schema(name),
                output_schema={},
                risk_level=risk,
                agent_owner="AppAgent",
                supports_dry_run=dry_run,
                requires_authorized_path=requires_path,
                execute=fn,
            )
        )
