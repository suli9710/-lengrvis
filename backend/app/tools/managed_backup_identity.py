"""Runtime-owned identity capture for managed rollback backups."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

MANAGED_BACKUP_IDENTITY_SCHEMA = "managed-backup-identity/v3"
MANAGED_BACKUP_IDENTITY_KEYS = frozenset(
    {
        "schema",
        "sha256",
        "size",
        "inode",
        "device",
        "object_id",
        "mtime_ns",
        "change_time_ns",
    }
)
MANAGED_BACKUP_IDENTITY_NONNEGATIVE_KEYS = ("size", "inode", "device")
MANAGED_BACKUP_IDENTITY_TIMESTAMP_KEYS = ("mtime_ns", "change_time_ns")
MANAGED_BACKUP_IDENTITY_OBJECT_KEYS = ("inode", "device", "object_id", "mtime_ns", "change_time_ns")

_CHUNK_BYTES = 1024 * 1024
_OBJECT_ID_PATTERN = re.compile(r"(?:win32:[0-9a-f]{16}:[0-9a-f]{32}|posix:[0-9]+:[0-9]+)\Z")
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_FILETIME_UNIX_EPOCH_TICKS = 116_444_736_000_000_000


def capture_managed_backup_identity(
    path: Path,
    *,
    abort_callback: Callable[[], None] | None = None,
    expected_size: int | None = None,
) -> dict[str, Any]:
    """Hash a regular file and bind metadata captured from the same open descriptor."""

    if expected_size is not None and (type(expected_size) is not int or expected_size < 0):
        raise ValueError("Managed backup expected size is invalid.")
    if abort_callback is not None:
        abort_callback()
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= int(getattr(os, name, 0))
    descriptor = os.open(path, flags)
    try:
        before = _descriptor_metadata(descriptor)
        if not stat.S_ISREG(before["mode"]):
            raise OSError("Managed backup is not a regular file.")
        if expected_size is not None and before["size"] != expected_size:
            raise OSError("Managed backup size changed after evidence capture.")

        digest = hashlib.sha256()
        bytes_read = 0
        remaining = before["size"]
        while remaining:
            if abort_callback is not None:
                abort_callback()
            chunk = os.read(descriptor, min(_CHUNK_BYTES, remaining))
            if not chunk:
                break
            bytes_read += len(chunk)
            remaining -= len(chunk)
            digest.update(chunk)
        if abort_callback is not None:
            abort_callback()

        after = _descriptor_metadata(descriptor)
        has_extra_data = bool(os.read(descriptor, 1))
        if before != after or bytes_read != before["size"] or has_extra_data:
            raise OSError("Managed backup changed while its identity was captured.")
        return {
            "schema": MANAGED_BACKUP_IDENTITY_SCHEMA,
            "sha256": digest.hexdigest(),
            "size": before["size"],
            "inode": before["inode"],
            "device": before["device"],
            "object_id": before["object_id"],
            "mtime_ns": before["mtime_ns"],
            "change_time_ns": before["change_time_ns"],
        }
    finally:
        os.close(descriptor)


def validate_managed_backup_identity(value: Any) -> dict[str, Any]:
    """Return a validated v3 identity or fail closed for legacy/malformed evidence."""

    if not isinstance(value, dict) or set(value) != MANAGED_BACKUP_IDENTITY_KEYS:
        raise ValueError("Managed backup identity is malformed.")
    if value.get("schema") != MANAGED_BACKUP_IDENTITY_SCHEMA:
        raise ValueError("Managed backup identity schema is unsupported.")
    digest = value.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("Managed backup identity digest is malformed.")
    if any(type(value.get(key)) is not int or value[key] < 0 for key in MANAGED_BACKUP_IDENTITY_NONNEGATIVE_KEYS):
        raise ValueError("Managed backup identity numbers are malformed.")
    if any(type(value.get(key)) is not int for key in MANAGED_BACKUP_IDENTITY_TIMESTAMP_KEYS):
        raise ValueError("Managed backup identity timestamps are malformed.")
    object_id = value.get("object_id")
    if not isinstance(object_id, str) or _OBJECT_ID_PATTERN.fullmatch(object_id) is None:
        raise ValueError("Managed backup object identity is malformed.")
    expected_prefix = "win32:" if sys.platform == "win32" else "posix:"
    if not object_id.startswith(expected_prefix):
        raise ValueError("Managed backup object identity is for another platform.")
    if object_id.startswith("posix:") and object_id != f"posix:{value['device']}:{value['inode']}":
        raise ValueError("Managed backup object identity is inconsistent.")
    return dict(value)


def _descriptor_metadata(descriptor: int) -> dict[str, Any]:
    descriptor_stat = os.fstat(descriptor)
    metadata: dict[str, Any] = {
        "mode": int(descriptor_stat.st_mode),
        "size": int(descriptor_stat.st_size),
        "inode": int(getattr(descriptor_stat, "st_ino", 0) or 0),
        "device": int(getattr(descriptor_stat, "st_dev", 0) or 0),
    }
    if sys.platform == "win32":
        metadata.update(_windows_descriptor_metadata(descriptor))
    else:
        metadata.update(
            {
                "object_id": f"posix:{metadata['device']}:{metadata['inode']}",
                "mtime_ns": int(getattr(descriptor_stat, "st_mtime_ns", 0) or 0),
                "change_time_ns": int(getattr(descriptor_stat, "st_ctime_ns", 0) or 0),
            }
        )
    return metadata


def _windows_descriptor_metadata(descriptor: int) -> dict[str, Any]:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("creation_time", ctypes.c_longlong),
            ("last_access_time", ctypes.c_longlong),
            ("last_write_time", ctypes.c_longlong),
            ("change_time", ctypes.c_longlong),
            ("file_attributes", wintypes.DWORD),
        ]

    class FileId128(ctypes.Structure):
        _fields_ = [("identifier", ctypes.c_ubyte * 16)]

    class FileIdInfo(ctypes.Structure):
        _fields_ = [("volume_serial_number", ctypes.c_ulonglong), ("file_id", FileId128)]

    handle = msvcrt.get_osfhandle(descriptor)
    if handle == -1:
        raise OSError("Managed backup descriptor has no Windows file handle.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL

    basic = FileBasicInfo()
    if not kernel32.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle),
        0,
        ctypes.byref(basic),
        ctypes.sizeof(basic),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, f"GetFileInformationByHandleEx(FileBasicInfo) failed: {ctypes.FormatError(error)}")
    if int(basic.file_attributes) & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError("Managed backup is a filesystem reparse point.")

    file_id = FileIdInfo()
    if not kernel32.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle),
        18,
        ctypes.byref(file_id),
        ctypes.sizeof(file_id),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, f"GetFileInformationByHandleEx(FileIdInfo) failed: {ctypes.FormatError(error)}")
    identifier = bytes(file_id.file_id.identifier).hex()
    return {
        "object_id": f"win32:{int(file_id.volume_serial_number):016x}:{identifier}",
        "mtime_ns": _windows_filetime_to_unix_ns(basic.last_write_time),
        "change_time_ns": _windows_filetime_to_unix_ns(basic.change_time),
    }


def _windows_filetime_to_unix_ns(value: int) -> int:
    return (int(value) - _WINDOWS_FILETIME_UNIX_EPOCH_TICKS) * 100
