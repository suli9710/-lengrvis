"""Execution-boundary checks shared by explicit and autonomous butler runs."""

from __future__ import annotations

import ctypes
import sys
from typing import Any

from app.core import butler_store as store
from app.core.butler_models import ButlerMatter, MatterStatus
from app.core.schemas import Task
from app.services.butler_scopes import action_scope, matter_scopes

REVERSIBLE_FILE_TOOLS = frozenset(
    {
        "file.copy",
        "file.move",
        "file.rename",
        "file.create_folder",
        "file.write_text",
        "file.write_json",
        "file.edit_text",
        "file.generate_markdown_report",
        "document.create_xlsx",
        "document.create_docx",
    }
)


class ButlerExecutionDeferred(RuntimeError):
    pass


def bound_matter(task: Task) -> ButlerMatter | None:
    matter_id = str(task.metadata.get("butler_matter_id") or "")
    if not matter_id:
        return None
    matter = store.get(ButlerMatter, matter_id)
    if (
        matter is None
        or matter.active_task_id != task.id
        or not matter.execution_ticket
        or task.metadata.get("butler_execution_ticket") != matter.execution_ticket
    ):
        raise ButlerExecutionDeferred("事项与执行记录不匹配，已停止操作。")
    return matter


def validate_task_action(
    task: Task, tool: Any, args: dict, *, live: bool = False, current_tool_call_id: str = ""
) -> ButlerMatter | None:
    matter = bound_matter(task)
    if matter is None:
        return None
    if store.control_state()["paused"] or matter.status in {
        MatterStatus.PAUSED,
        MatterStatus.CANCELLED,
        MatterStatus.COMPLETED,
    }:
        raise ButlerExecutionDeferred("管家或事项已暂停，尚未执行新的动作。")
    scopes = matter_scopes(matter)
    from app.services.butler_effects import repeated_submission

    if repeated_submission(matter, tool.name, args, current_tool_call_id=current_tool_call_id):
        raise ButlerExecutionDeferred("这项提交已有执行记录。请读取原网页回执核对结果，不能再次提交。")
    pure_data = tool.name == "workflow.merge_data" and getattr(tool, "trust_tier", "") == "builtin"
    required_action = "observe"
    if str(getattr(tool, "risk_level", "R4")).startswith(("R2", "R3", "R4")):
        if tool.name.startswith(("file.", "document.")):
            required_action = "file_modify"
        elif tool.name.startswith("ui_automation."):
            required_action = "ui_edit"
        elif tool.name.startswith("browser."):
            required_action = "browser_prepare"
            if tool.name == "browser.act":
                from app.services.browser_activity_runtime import _normalize_action

                if _normalize_action(args).get("kind") in {"observe", "wait", "screenshot", "open", "navigate"}:
                    required_action = "observe"
    if (
        matter.source != "user"
        and not pure_data
        and action_scope(tool.name, args, scopes, action=required_action) is None
    ):
        raise ButlerExecutionDeferred("主动事项的操作超出已授权来源。")
    if (
        live
        and matter.source != "user"
        and tool.name in {"ui_automation.focus", "ui_automation.focus_window"}
        and not desktop_ready()
    ):
        raise ButlerExecutionDeferred("等待桌面空闲后继续。")
    return matter


def defer_task_for_desktop(task: Task, reason: str) -> None:
    from app.core.butler_models import WaitCondition

    matter = bound_matter(task)
    if matter is None or matter.status in {MatterStatus.PAUSED, MatterStatus.CANCELLED}:
        return

    def update(item: ButlerMatter) -> None:
        item.status = MatterStatus.WAITING_EVENT
        item.waiting = WaitCondition(kind="desktop_idle")
        item.progress = reason

    store.mutate(ButlerMatter, matter.id, update)


def tool_context_for_task(task: Task) -> dict[str, Any]:
    matter = bound_matter(task)
    if matter is None:
        return {}
    scopes = matter_scopes(matter)
    result: dict[str, Any] = {"butler_matter_id": matter.id, "butler_scope_versions": dict(matter.scope_versions)}
    if matter.artifacts or any(c.kind == "office_artifacts" for c in matter.completion_conditions):
        from app.core.content_provenance import restore_untrusted_content
        from app.services.butler_evidence import reviewed_results

        # Replanning with saved page/workbook contents must retain their taint.
        result["upstream_content_envelopes"] = [
            (
                output.content_envelope
                if call["task_id"] == task.id
                else restore_untrusted_content(
                    output.content_envelope,
                    output.output,
                    task_scope=task.id,
                    source_id=call["id"],
                )
            ).model_dump(mode="json")
            for call, output in reviewed_results(matter)
            if output.content_envelope is not None
        ]
    from app.llm.registry import get_effective_settings

    roots = list(get_effective_settings().allowed_directories) if matter.source == "user" else []
    result["allowed_directories"] = list(
        dict.fromkeys([*roots, *(path for scope in scopes for path in scope.directories)])
    )
    return result


def reversible_scope(task: Task, tool: Any, args: dict, preview: dict) -> str | None:
    matter = validate_task_action(task, tool, args)
    if matter is None or tool.name not in REVERSIBLE_FILE_TOOLS or tool.trust_tier != "builtin":
        return None
    if tool.compensation_strength not in {"reversible", "transactional"}:
        return None
    if not preview.get("_resource_state") or preview.get("error"):
        return None
    scope = action_scope(tool.name, args, matter_scopes(matter), action="file_modify")
    return scope.id if scope else None


def desktop_ready() -> bool:
    if sys.platform != "win32":
        return True
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        desktop = user32.OpenInputDesktop(0, False, 1)
        if not desktop:
            return False
        user32.CloseDesktop(desktop)

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint32), ("dwTime", ctypes.c_uint32)]

        last = LastInput(ctypes.sizeof(LastInput), 0)
        if not user32.GetLastInputInfo(ctypes.byref(last)):
            return False
        if (ctypes.windll.kernel32.GetTickCount() - last.dwTime) & 0xFFFFFFFF < 3000:
            return False
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        from ctypes import wintypes

        rect = wintypes.RECT()
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            if (
                rect.left <= 0
                and rect.top <= 0
                and rect.right >= user32.GetSystemMetrics(0)
                and rect.bottom >= user32.GetSystemMetrics(1)
            ):
                return False
        return True
    except (OSError, AttributeError, ctypes.ArgumentError):
        return False
