"""Small Windows Job Object wrapper used for untrusted local handlers.

The module is import-safe on non-Windows hosts and deliberately exposes a
fail-closed attach operation.  A caller that requests isolation must not fall
back to an ordinary process when creating or assigning the Job Object fails.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Any


class WindowsJobIsolationError(RuntimeError):
    """Raised when a requested Windows process isolation boundary is unavailable."""


@dataclass(frozen=True, slots=True)
class WindowsJobLimits:
    active_processes: int = 16
    process_memory_bytes: int = 512 * 1024 * 1024
    job_memory_bytes: int = 1024 * 1024 * 1024


JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", ctypes.c_uint32),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32),
        ("scheduling_class", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _BasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


def windows_job_supported() -> bool:
    return os.name == "nt"


def attach_process_to_job(process: Any, limits: WindowsJobLimits | None = None) -> int:
    """Create a kill-on-close Job Object and assign ``process`` to it.

    The returned handle must remain open for the lifetime of the process and
    be closed by :func:`close_job_handle`.  No non-Windows fallback is allowed.
    """

    if os.name != "nt":
        raise WindowsJobIsolationError("Windows Job Object isolation is unavailable on this host")
    pid = int(getattr(process, "pid", 0) or 0)
    if pid <= 0:
        raise WindowsJobIsolationError("cannot isolate a process without a valid pid")
    limits = limits or WindowsJobLimits()
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_job = kernel32.CreateJobObjectW
        create_job.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        create_job.restype = ctypes.c_void_p
        set_info = kernel32.SetInformationJobObject
        set_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        set_info.restype = ctypes.c_int
        assign = kernel32.AssignProcessToJobObject
        assign.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        assign.restype = ctypes.c_int
        handle = int(create_job(None, None) or 0)
        if not handle:
            raise _last_windows_error("CreateJobObjectW")
        info = _ExtendedLimitInformation()
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if limits.active_processes > 0:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.basic_limit_information.active_process_limit = int(limits.active_processes)
        if limits.process_memory_bytes > 0:
            flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.process_memory_limit = int(limits.process_memory_bytes)
        if limits.job_memory_bytes > 0:
            flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            info.job_memory_limit = int(limits.job_memory_bytes)
        info.basic_limit_information.limit_flags = flags
        if not set_info(
            ctypes.c_void_p(handle),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            close_job_handle(handle)
            raise _last_windows_error("SetInformationJobObject")
        process_handle = _open_process_handle(kernel32, pid)
        assigned = False
        try:
            if not assign(ctypes.c_void_p(handle), ctypes.c_void_p(process_handle)):
                raise _last_windows_error("AssignProcessToJobObject")
            assigned = True
        finally:
            if not assigned:
                close_job_handle(handle)
            _close_kernel_handle(kernel32, process_handle)
        return handle
    except WindowsJobIsolationError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise WindowsJobIsolationError(f"Windows Job Object isolation failed: {type(exc).__name__}") from exc


def resume_suspended_process(process: Any) -> None:
    """Resume a process created with ``CREATE_SUSPENDED``.

    Isolation callers create untrusted children suspended so their first
    instruction cannot run before Job Object assignment.  CPython retains the
    native process handle on ``Popen._handle``; absence of that handle is a
    hard isolation failure rather than a reason to run the child normally.
    """

    if os.name != "nt":
        raise WindowsJobIsolationError("Suspended Windows process resumption is unavailable on this host")
    process_handle = int(getattr(process, "_handle", 0) or 0)
    if process_handle <= 0:
        raise WindowsJobIsolationError("cannot resume an isolated process without a native process handle")
    try:
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        resume_process = ntdll.NtResumeProcess
        resume_process.argtypes = [ctypes.c_void_p]
        resume_process.restype = ctypes.c_long
        status = int(resume_process(ctypes.c_void_p(process_handle)))
        if status != 0:
            raise WindowsJobIsolationError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xFFFFFFFF:08X}")
    except WindowsJobIsolationError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise WindowsJobIsolationError(f"Suspended Windows process resumption failed: {type(exc).__name__}") from exc


def close_job_handle(handle: int | None) -> None:
    if not handle or os.name != "nt":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        close_handle(ctypes.c_void_p(int(handle)))
    except (AttributeError, OSError, TypeError, ValueError):
        return


def _open_process_handle(kernel32: Any, pid: int) -> int:
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    # PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
    desired_access = 0x0100 | 0x0001 | 0x1000
    handle = int(open_process(desired_access, False, pid) or 0)
    if not handle:
        raise _last_windows_error("OpenProcess")
    return handle


def _close_kernel_handle(kernel32: Any, handle: int) -> None:
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    close_handle(ctypes.c_void_p(int(handle)))


def _last_windows_error(operation: str) -> WindowsJobIsolationError:
    error = ctypes.get_last_error()
    return WindowsJobIsolationError(f"{operation} failed with Windows error {error}")
