from __future__ import annotations

from pathlib import PurePath
from typing import Any

from app.connectors.base import ToolBackedConnector
from app.connectors.contracts import (
    ConnectorDescriptor,
    ConnectorOutcome,
    ConnectorPhase,
    ConnectorRequest,
    ConnectorResult,
    ToolInvocation,
    ToolInvoker,
)
from app.connectors.registry import ConnectorRegistry


class SpreadsheetConnector(ToolBackedConnector):
    descriptor = ConnectorDescriptor(
        connector_id="spreadsheet",
        version="1.0.0",
        display_name="Spreadsheet",
        description="Semantic Excel/CSV observation and editing through governed tools.",
        formats=[".csv", ".xlsx", ".xlsm"],
        semantic_actions=["analyze", "read", "replace_text", "write_cell", "write_text"],
        application_families=["microsoft_excel", "wps_spreadsheets"],
        tool_mappings={
            "probe": ["app.excel.status", "file.get_metadata"],
            "observe": ["document.analyze_csv", "document.analyze_xlsx"],
            "execute": ["app.excel.write_cell", "document.edit_xlsx", "file.edit_text", "file.write_text"],
            "verify": ["document.analyze_csv", "document.analyze_xlsx", "file.read_text"],
        },
    )

    def _build_probe_invocation(self, request: ConnectorRequest) -> ToolInvocation:
        path = str(request.parameters.get("path") or "")
        if path and _suffix(path) == ".csv":
            return self._tool_invocation(
                request,
                phase=ConnectorPhase.PROBE,
                tool_name="file.get_metadata",
                args={"path": path},
                has_side_effect=False,
            )
        return self._tool_invocation(
            request,
            phase=ConnectorPhase.PROBE,
            tool_name="app.excel.status",
            args={},
            has_side_effect=False,
        )

    def _build_observe_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        path = str(request.parameters.get("path") or "")
        suffix = _suffix(path)
        if suffix == ".csv":
            tool_name = "document.analyze_csv" if request.action in {"analyze", "read"} else "file.read_text"
        elif suffix in {".xlsx", ".xlsm"}:
            tool_name = "document.analyze_xlsx"
        else:
            return None
        return self._tool_invocation(
            request,
            phase=ConnectorPhase.OBSERVE,
            tool_name=tool_name,
            args={"path": path},
            has_side_effect=False,
        )

    def _build_execute_invocation(
        self,
        request: ConnectorRequest,
        *,
        phase: ConnectorPhase,
    ) -> ToolInvocation | None:
        params = request.parameters
        path = str(params.get("path") or "")
        suffix = _suffix(path)
        action = request.action
        if action == "write_cell" and suffix in {".xlsx", ".xlsm"}:
            use_excel_com = str(params.get("engine") or "").casefold() == "excel_com"
            tool_name = "app.excel.write_cell" if use_excel_com else "document.edit_xlsx"
            args = _copy_args(params, "path", "sheet", "cell", "value", "approved", "approval_id")
        elif action == "replace_text" and suffix == ".csv":
            tool_name = "file.edit_text"
            args = _copy_args(params, "path", "old_string", "new_string", "replace_all", "approved", "approval_id")
        elif action == "write_text" and suffix == ".csv":
            tool_name = "file.write_text"
            args = _copy_args(params, "path", "text", "approved", "approval_id")
        else:
            return None
        args["dry_run"] = False
        return self._tool_invocation(
            request,
            phase=phase,
            tool_name=tool_name,
            args=args,
            has_side_effect=True,
        )

    def _build_verify_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        invocation = self._build_observe_invocation(request)
        if invocation is None:
            return None
        return invocation.model_copy(update={"phase": ConnectorPhase.VERIFY})

    def _build_recover_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        params = request.parameters
        rollback = params.get("rollback_info")
        if not isinstance(rollback, dict):
            return None
        path = str(rollback.get("path") or params.get("path") or "")
        operation = str(rollback.get("operation") or request.action)
        if operation in {"app.excel.write_cell", "document.edit_xlsx", "write_cell"} and "previous_value" in rollback:
            tool_name = "app.excel.write_cell" if operation == "app.excel.write_cell" else "document.edit_xlsx"
            args = {
                "path": path,
                "sheet": rollback.get("sheet") or params.get("sheet"),
                "cell": rollback.get("cell") or params.get("cell"),
                "value": rollback["previous_value"],
                "dry_run": False,
            }
        elif operation in {"file.edit_text", "replace_text"}:
            old_string = rollback.get("new_string") or params.get("new_string")
            new_string = rollback.get("old_string") or params.get("old_string")
            if old_string is None or new_string is None:
                return None
            tool_name = "file.edit_text"
            args = {
                "path": path,
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": bool(params.get("replace_all")),
                "dry_run": False,
            }
        else:
            return None
        return self._tool_invocation(
            request,
            phase=ConnectorPhase.RECOVER,
            tool_name=tool_name,
            args=args,
            has_side_effect=True,
        )


class OfficeDocumentConnector(ToolBackedConnector):
    descriptor = ConnectorDescriptor(
        connector_id="office_document",
        version="1.0.0",
        display_name="Office Document",
        description="Application-neutral document semantics for Microsoft Office and WPS Office.",
        formats=[".docx", ".xlsx", ".pptx"],
        semantic_actions=["extract_text", "open", "replace_text", "template_modify", "write_cell"],
        application_families=["microsoft_office", "wps_office"],
        tool_mappings={
            "probe": ["app.list_installed"],
            "observe": ["document.extract_text", "document.analyze_xlsx"],
            "execute": ["app.open_file", "document.edit_docx", "document.edit_xlsx", "document.edit_pptx"],
            "verify": ["document.extract_text", "document.analyze_xlsx"],
        },
    )

    def _build_probe_invocation(self, request: ConnectorRequest) -> ToolInvocation:
        return self._tool_invocation(
            request,
            phase=ConnectorPhase.PROBE,
            tool_name="app.list_installed",
            args={},
            has_side_effect=False,
        )

    def _build_observe_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        path = str(request.parameters.get("path") or "")
        suffix = _suffix(path)
        if suffix not in {".docx", ".xlsx", ".pptx"}:
            return None
        tool_name = "document.analyze_xlsx" if suffix == ".xlsx" else "document.extract_text"
        return self._tool_invocation(
            request,
            phase=ConnectorPhase.OBSERVE,
            tool_name=tool_name,
            args={"path": path},
            has_side_effect=False,
        )

    def _build_execute_invocation(
        self,
        request: ConnectorRequest,
        *,
        phase: ConnectorPhase,
    ) -> ToolInvocation | None:
        params = request.parameters
        path = str(params.get("path") or "")
        suffix = _suffix(path)
        action = request.action
        if action == "open" and suffix in {".docx", ".xlsx", ".pptx"}:
            tool_name = "app.open_file"
            args = {"path": path, "dry_run": False}
        elif action in {"replace_text", "template_modify"} and suffix in {".docx", ".pptx"}:
            tool_name = "document.edit_docx" if suffix == ".docx" else "document.edit_pptx"
            args = _copy_args(params, "path", "find", "replace", "approved", "approval_id")
            args["dry_run"] = False
        elif action in {"write_cell", "template_modify"} and suffix == ".xlsx":
            tool_name = "document.edit_xlsx"
            args = _copy_args(params, "path", "sheet", "cell", "value", "approved", "approval_id")
            args["dry_run"] = False
        else:
            return None
        return self._tool_invocation(
            request,
            phase=phase,
            tool_name=tool_name,
            args=args,
            has_side_effect=True,
        )

    def _build_verify_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        invocation = self._build_observe_invocation(request)
        if invocation is None:
            return None
        return invocation.model_copy(update={"phase": ConnectorPhase.VERIFY})

    def _build_recover_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        params = request.parameters
        rollback = params.get("rollback_info")
        path = str(params.get("path") or "")
        suffix = _suffix(path)
        if suffix in {".docx", ".pptx"} and request.action in {"replace_text", "template_modify"}:
            find = params.get("replace")
            replace = params.get("find")
            if find is None or replace is None:
                return None
            tool_name = "document.edit_docx" if suffix == ".docx" else "document.edit_pptx"
            args = {"path": path, "find": find, "replace": replace, "dry_run": False}
        elif suffix == ".xlsx" and isinstance(rollback, dict) and "previous_value" in rollback:
            tool_name = "document.edit_xlsx"
            args = {
                "path": path,
                "sheet": rollback.get("sheet") or params.get("sheet"),
                "cell": rollback.get("cell") or params.get("cell"),
                "value": rollback["previous_value"],
                "dry_run": False,
            }
        else:
            return None
        return self._tool_invocation(
            request,
            phase=ConnectorPhase.RECOVER,
            tool_name=tool_name,
            args=args,
            has_side_effect=True,
        )


class ControlledBrowserConnector(ToolBackedConnector):
    descriptor = ConnectorDescriptor(
        connector_id="controlled_browser",
        version="1.0.0",
        display_name="Controlled Browser",
        description="Isolated browser observation and approved form workflows.",
        formats=["https://", "http://"],
        semantic_actions=["open", "navigate", "click", "fill", "submit"],
        application_families=["lengrvis_browser_host"],
        tool_mappings={
            "probe": ["browser.sessions"],
            "observe": ["browser.observe"],
            "execute": [
                "browser.open_url",
                "browser.navigate",
                "browser.click_element",
                "browser.fill_form",
                "browser.submit_form",
            ],
            "verify": ["browser.observe", "browser.wait_for_selector"],
            "recover": ["browser.navigate", "browser.session_close"],
        },
    )

    def _build_probe_invocation(self, request: ConnectorRequest) -> ToolInvocation:
        return self._tool_invocation(
            request,
            phase=ConnectorPhase.PROBE,
            tool_name="browser.sessions",
            args={},
            has_side_effect=False,
        )

    def _build_observe_invocation(self, request: ConnectorRequest) -> ToolInvocation:
        args = _copy_args(request.parameters, "session_id", "url", "max_chars", "task_id", "step_id")
        return self._tool_invocation(
            request,
            phase=ConnectorPhase.OBSERVE,
            tool_name="browser.observe",
            args=args,
            has_side_effect=False,
        )

    def _build_execute_invocation(
        self,
        request: ConnectorRequest,
        *,
        phase: ConnectorPhase,
    ) -> ToolInvocation | None:
        params = request.parameters
        mappings: dict[str, tuple[str, tuple[str, ...]]] = {
            "open": ("browser.open_url", ("url", "mode", "use_system_browser")),
            "navigate": ("browser.navigate", ("url", "session_id")),
            "click": ("browser.click_element", ("url", "session_id", "selector", "approved", "approval_id")),
            "fill": ("browser.fill_form", ("url", "session_id", "fields", "approved", "approval_id")),
            "submit": ("browser.submit_form", ("url", "session_id", "selector", "approved", "approval_id")),
        }
        mapping = mappings.get(request.action)
        if mapping is None:
            return None
        tool_name, keys = mapping
        args = _copy_args(params, *keys)
        args["dry_run"] = False
        return self._tool_invocation(
            request,
            phase=phase,
            tool_name=tool_name,
            args=args,
            has_side_effect=True,
        )

    def _build_verify_invocation(self, request: ConnectorRequest) -> ToolInvocation:
        params = request.parameters
        selector = params.get("receipt_selector") or params.get("verify_selector")
        if selector:
            return self._tool_invocation(
                request,
                phase=ConnectorPhase.VERIFY,
                tool_name="browser.wait_for_selector",
                args=_copy_args(
                    {
                        **params,
                        "selector": selector,
                        "timeout_ms": params.get("timeout_ms", 10000),
                    },
                    "url",
                    "session_id",
                    "selector",
                    "timeout_ms",
                ),
                has_side_effect=False,
            )
        invocation = self._build_observe_invocation(request)
        return invocation.model_copy(update={"phase": ConnectorPhase.VERIFY})

    def _build_recover_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        params = request.parameters
        if params.get("recovery_url"):
            return self._tool_invocation(
                request,
                phase=ConnectorPhase.RECOVER,
                tool_name="browser.navigate",
                args={
                    "url": params["recovery_url"],
                    "session_id": params.get("session_id"),
                    "dry_run": False,
                },
                has_side_effect=True,
            )
        if params.get("close_session") and params.get("session_id"):
            return self._tool_invocation(
                request,
                phase=ConnectorPhase.RECOVER,
                tool_name="browser.session_close",
                args={"session_id": params["session_id"]},
                has_side_effect=True,
            )
        return None


class DesktopNotificationConnector(ToolBackedConnector):
    descriptor = ConnectorDescriptor(
        connector_id="desktop_notification",
        version="1.0.0",
        display_name="Desktop Notification",
        description="Task-inbox and desktop notifications through the governed notification capability.",
        semantic_actions=["send"],
        application_families=["lengrvis_desktop"],
        tool_mappings={"execute": ["notification.send"]},
    )

    def _build_execute_invocation(
        self,
        request: ConnectorRequest,
        *,
        phase: ConnectorPhase,
    ) -> ToolInvocation | None:
        if request.action != "send":
            return None
        args = _copy_args(request.parameters, "title", "body", "severity", "task_id")
        args.setdefault("task_id", request.context.task_id)
        return self._tool_invocation(
            request,
            phase=phase,
            tool_name="notification.send",
            args=args,
            has_side_effect=True,
        )

    def _build_verify_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        return None

    async def verify(self, request: ConnectorRequest) -> ConnectorResult:
        receipt = request.parameters.get("receipt")
        verified = isinstance(receipt, dict) and bool(receipt.get("queued"))
        return self._result(
            request,
            phase=ConnectorPhase.VERIFY,
            outcome=ConnectorOutcome.VERIFIED if verified else ConnectorOutcome.FAILED,
            ok=verified,
            output={"verified": verified, "receipt": receipt if isinstance(receipt, dict) else {}},
            error="" if verified else "Notification receipt did not confirm queueing.",
        )


def build_builtin_connector_registry(invoker: ToolInvoker) -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(SpreadsheetConnector(invoker))
    registry.register(OfficeDocumentConnector(invoker))
    registry.register(ControlledBrowserConnector(invoker))
    registry.register(DesktopNotificationConnector(invoker))
    return registry


def _suffix(path: str) -> str:
    return PurePath(path).suffix.casefold()


def _copy_args(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source[key] for key in keys if key in source and source[key] is not None}
