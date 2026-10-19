"""Open exact Office documents in dedicated instances without touching other work."""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.tools.tool_abort import raise_if_tool_aborted

_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="office-open")


def _check_current_artifact(context: dict) -> None:
    if not context.get("office_matter_id"):
        return
    from app.core import butler_store as store
    from app.core.butler_models import ButlerMatter
    from app.services.office_artifacts import context_matter, find_artifact, verify_artifact

    matter = store.get(ButlerMatter, context["office_matter_id"])
    if context.get("butler_matter_id"):
        matter = context_matter(context)
    if matter is None:
        raise ValueError("成果事项已不存在。")
    verify_artifact(matter, find_artifact(matter, context["office_artifact_id"]))


def _open(path: Path, application: str, context: dict, deadline_cancel: threading.Event) -> dict:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    document = None
    handed_over = False
    try:
        raise_if_tool_aborted(context)
        if deadline_cancel.is_set():
            raise TimeoutError("Office open deadline expired.")
        _check_current_artifact(context)
        app = win32com.client.DispatchEx("Excel.Application" if application == "excel" else "Word.Application")
        app.AutomationSecurity = 3
        if int(app.AutomationSecurity) != 3:
            raise ValueError("Office could not disable active content.")
        app.DisplayAlerts = False if application == "excel" else 0
        if application == "excel":
            document = app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
        else:
            app.Options.UpdateLinksAtOpen = False
            document = app.Documents.Open(str(path), ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False)
        if os.path.normcase(str(Path(document.FullName).resolve())) != os.path.normcase(str(path.resolve())):
            raise ValueError("Office opened a different document.")
        raise_if_tool_aborted(context)
        _check_current_artifact(context)
        if deadline_cancel.is_set():
            raise TimeoutError("Office open deadline expired.")
        app.Visible = True
        if application == "excel":
            hwnd = int(app.Hwnd)
        else:
            hwnd = int(document.ActiveWindow.Hwnd)
        handed_over = True
        return {
            "ok": True,
            "path": str(path),
            "application": application,
            "opened": True,
            "verified": True,
            "window_handle": hwnd,
            "read_only": True,
        }
    finally:
        if not handed_over and app is not None:
            try:
                if document is not None:
                    document.Close(False)
                app.Quit()
            except pythoncom.com_error as exc:
                logging.getLogger(__name__).warning("Owned Office instance cleanup failed: %s", type(exc).__name__)
        document = app = None
        pythoncom.CoUninitialize()


def open_office_document(path: Path, application: str, context: dict) -> dict:
    if application not in {"excel", "word"} or path.suffix.lower() != {"excel": ".xlsx", "word": ".docx"}[application]:
        raise ValueError("指定应用与文档格式不匹配，仅支持 Excel .xlsx 与 Word .docx。")
    if os.name != "nt":
        raise ValueError("打开指定 Office 应用需要 Windows。")
    deadline_cancel = threading.Event()
    future = _worker.submit(_open, path, application, context, deadline_cancel)
    try:
        return future.result(timeout=45)
    except TimeoutError:
        deadline_cancel.set()
        future.cancel()
        raise
