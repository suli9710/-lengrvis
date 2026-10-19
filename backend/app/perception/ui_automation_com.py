"""Typed Windows UIA access on a process-lifetime, non-UI MTA thread.

Keep the originating apartment alive while element references exist. Callers
must dispatch every native element operation through this worker; plain data
can cross the boundary. Optional comtypes imports stay lazy for other hosts.
"""

from __future__ import annotations

import asyncio
import contextvars
import ctypes
import importlib
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from typing import Any, TypeVar

_T = TypeVar("_T")
_thread = threading.local()


def _initialize_mta() -> None:
    import comtypes

    comtypes.CoInitializeEx(0)  # COINIT_MULTITHREADED; this thread owns no windows.
    _thread.is_worker = True


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lengrvis-uia-mta", initializer=_initialize_mta)


def _submit(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> Future[_T]:
    # comtypes automatically initializes its first importing thread as STA.
    # Import before starting our worker, then initialize the worker as MTA.
    import comtypes.client  # noqa: F401

    context = contextvars.copy_context()
    return _executor.submit(context.run, partial(fn, *args, **kwargs))


def run_sync(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    if getattr(_thread, "is_worker", False):
        return fn(*args, **kwargs)
    return _submit(fn, *args, **kwargs).result()


async def run_async(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    if getattr(_thread, "is_worker", False):
        return fn(*args, **kwargs)
    return await asyncio.wrap_future(_submit(fn, *args, **kwargs))


def create_automation() -> Any:
    import comtypes.client

    # Resolve the trusted Windows system library, not a DLL in the cwd/PATH.
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("Could not resolve the Windows system directory for UIAutomationCore.")
    module = comtypes.client.GetModule(buffer.value + "\\UIAutomationCore.dll")
    return comtypes.client.CreateObject(module.CUIAutomation, interface=module.IUIAutomation)


def process_windows(automation: Any, process_id: int) -> list[Any]:
    """Resolve this process's window roots, including hosted app subwindows.

    Keep COM calls outside EnumWindows' callback. A UIA FindAll over the desktop
    invokes providers belonging to unrelated applications even with a PID
    condition, and an unrelated provider can fail or block the entire search.
    """
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    handles = []

    def belongs_to_process(hwnd: int) -> bool:
        owner = wintypes.DWORD()
        return bool(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))) and owner.value == process_id

    @callback_type
    def collect_hosted(hwnd: int, _lparam: int) -> bool:
        if not belongs_to_process(hwnd) or not user32.IsWindowVisible(hwnd):
            return True
        # A UWP app can be a child of ApplicationFrameHost. Return its own
        # subtree once, never the unrelated host or each individual control.
        ancestor = user32.GetAncestor(hwnd, 1)  # GA_PARENT
        while ancestor:
            if belongs_to_process(ancestor):
                return True
            ancestor = user32.GetAncestor(ancestor, 1)
        handles.append(hwnd)
        return True

    @callback_type
    def collect(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if belongs_to_process(hwnd):
            handles.append(hwnd)
        else:
            user32.EnumChildWindows(hwnd, collect_hosted, 0)
        return True

    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return [automation.ElementFromHandle(hwnd) for hwnd in handles]


def current_pattern(native: Any, pattern_id: int, interface_name: str) -> Any:
    pattern = native.GetCurrentPattern(pattern_id)
    if hasattr(pattern, "QueryInterface"):
        module = importlib.import_module("comtypes.gen.UIAutomationClient")
        return pattern.QueryInterface(getattr(module, interface_name))
    # Injected adapters may already return an interface (e.g. a test provider).
    return pattern


def edit_control_text(native: Any, max_length: int) -> str | None:
    """Read a standard HWND edit without the legacy UIA value's 4K clipping.

    Other control types keep their UIA pattern path. WM_GETTEXT is a system
    message, so Windows marshals its bounded buffer across processes.
    """
    # Injected providers own their value semantics. Only a native COM element
    # may delegate a read to an actual HWND.
    if sys.platform != "win32" or not hasattr(native, "QueryInterface"):
        return None
    hwnd = int(getattr(native, "CurrentNativeWindowHandle", 0) or 0)
    if not hwnd:
        return None

    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    class_name = ctypes.create_unicode_buffer(256)
    if not user32.GetClassNameW(hwnd, class_name, len(class_name)):
        raise OSError("Could not identify the native text control.")
    name = class_name.value.casefold()
    if name != "edit" and not name.startswith("windowsforms10.edit."):
        return None

    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    owner = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)) or owner.value != int(
        getattr(native, "CurrentProcessId", 0) or 0
    ):
        raise OSError("The native text control's process identity changed.")

    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    ctypes.set_last_error(0)
    style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
    if not style and ctypes.get_last_error():
        raise OSError("Could not check the native text control's password style.")
    if style & 0x0020:  # ES_PASSWORD; also check the native control, not just UIA.
        return ""

    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    # One extra UTF-16 unit detects oversize text; one more holds the NUL.
    buffer = ctypes.create_unicode_buffer(max_length + 2)
    copied = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(
        hwnd,
        0x000D,  # WM_GETTEXT
        len(buffer),
        ctypes.addressof(buffer),
        0x0023,  # SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT
        2000,
        ctypes.byref(copied),
    ):
        raise OSError("Native text retrieval failed or timed out.")
    if copied.value > max_length:
        raise OSError("UI text exceeds the supported text limit; use a document or file tool.")
    return ctypes.wstring_at(buffer, copied.value)
