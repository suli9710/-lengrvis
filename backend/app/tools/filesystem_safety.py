from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from app.core.errors import SecurityError
from app.core.paths import normalize_path, path_within_explicit_scope
from app.tools.tool_abort import raise_if_tool_aborted


def prepare_parent_for_mutation(path: Path, allowed: list[str], context: dict[str, Any] | None = None) -> None:
    raise_if_tool_aborted(context)
    ensure_mutation_path_safe(path.parent, allowed, include_self=True, context=context)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_mutation_path_safe(path.parent, allowed, include_self=True, context=context)


def ensure_mutation_path_safe(
    path: Path,
    allowed: list[str],
    *,
    include_self: bool,
    context: dict[str, Any] | None = None,
) -> None:
    target = path if include_self else path.parent
    real_target = target.expanduser().resolve(strict=False)
    scope = _explicit_scope(context) if context else None
    base = authorized_real_base(real_target, allowed, explicit_scope_text=scope)
    reject_reparse_points(base, target)


def safe_write_text(path: Path, text: str, allowed: list[str], context: dict[str, Any] | None = None) -> None:
    raise_if_tool_aborted(context)
    prepare_parent_for_mutation(path, allowed, context)
    ensure_mutation_path_safe(path, allowed, include_self=path_exists_or_reparse_point(path), context=context)
    if sys.platform == "win32":
        write_text_with_windows_handle(path, text, allowed, context)
        return
    if supports_dir_fd_no_follow():
        write_text_with_dir_fd_no_follow(path, text)
        return
    path.write_text(text, encoding="utf-8")


def safe_copy_file(src: Path, dst: Path, allowed: list[str], context: dict[str, Any] | None = None) -> None:
    safe_copy_file_between_scopes(src, dst, allowed, allowed, context)


def safe_copy_file_between_scopes(
    src: Path,
    dst: Path,
    src_allowed: list[str],
    dst_allowed: list[str],
    context: dict[str, Any] | None = None,
) -> None:
    raise_if_tool_aborted(context)
    ensure_mutation_path_safe(src, src_allowed, include_self=True, context=context)
    prepare_parent_for_mutation(dst, dst_allowed, context)
    ensure_mutation_path_safe(dst, dst_allowed, include_self=path_exists_or_reparse_point(dst), context=context)
    if sys.platform == "win32":
        copy_file_with_windows_handles(src, dst, src_allowed, dst_allowed, context)
        return
    shutil.copy2(src, dst)


def safe_move_file(src: Path, dst: Path, allowed: list[str], context: dict[str, Any] | None = None) -> None:
    raise_if_tool_aborted(context)
    ensure_mutation_path_safe(src, allowed, include_self=True, context=context)
    safe_copy_file(src, dst, allowed, context)
    raise_if_tool_aborted(context)
    ensure_mutation_path_safe(src, allowed, include_self=True, context=context)
    if sys.platform == "win32":
        delete_file_with_windows_handle(src, allowed, context)
        return
    src.unlink()


def authorized_real_base(
    real_target: Path,
    allowed: list[str],
    *,
    explicit_scope_text: str | None = None,
) -> Path:
    if allowed:
        for raw_base in allowed:
            base = Path(raw_base).expanduser().resolve(strict=False)
            try:
                if real_target == base or real_target.is_relative_to(base):
                    return base
            except ValueError:
                continue
        raise SecurityError("Path resolves outside authorized directories.")
    if explicit_scope_text and path_within_explicit_scope(real_target, explicit_scope_text):
        from app.agents.path_detection import find_explicit_path

        explicit_raw = find_explicit_path(explicit_scope_text)
        if explicit_raw:
            explicit = normalize_path(explicit_raw)
            if explicit.is_dir():
                return explicit
            return explicit.parent
    raise SecurityError("No authorized directories configured.")


def reject_reparse_points(base: Path, target: Path) -> None:
    try:
        relative = target.relative_to(base)
    except ValueError:
        return

    current = base
    for part in relative.parts:
        current = current / part
        if is_reparse_point(current):
            raise SecurityError("Filesystem links inside authorized directories are not writable.")


def path_exists_or_reparse_point(path: Path) -> bool:
    return path.exists() or is_reparse_point(path)


def is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def supports_dir_fd_no_follow() -> bool:
    return hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def write_text_with_dir_fd_no_follow(path: Path, text: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    mode = 0o666
    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path.name, flags, mode, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(text)
        finally:
            if fd >= 0:
                os.close(fd)
    finally:
        os.close(dir_fd)


def write_text_with_windows_handle(
    path: Path,
    text: str,
    allowed: list[str],
    context: dict[str, Any] | None = None,
) -> None:
    import msvcrt

    handle = _open_windows_file_handle(path, access=_win_generic_write(), creation=_win_create_always())
    try:
        _assert_windows_handle_authorized(handle, allowed, context)
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        handle = None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    finally:
        if handle is not None:
            _close_windows_handle(handle)


def copy_file_with_windows_handles(
    src: Path,
    dst: Path,
    src_allowed: list[str],
    dst_allowed: list[str],
    context: dict[str, Any] | None = None,
) -> None:
    import msvcrt

    src_handle = _open_windows_file_handle(src, access=_win_generic_read(), creation=_win_open_existing())
    dst_handle = None
    try:
        _assert_windows_handle_authorized(src_handle, src_allowed, context)
        dst_handle = _open_windows_file_handle(dst, access=_win_generic_write(), creation=_win_create_always())
        _assert_windows_handle_authorized(dst_handle, dst_allowed, context)
        src_fd = msvcrt.open_osfhandle(src_handle, os.O_RDONLY)
        src_handle = None
        dst_fd = msvcrt.open_osfhandle(dst_handle, os.O_WRONLY)
        dst_handle = None
        with os.fdopen(src_fd, "rb") as src_fh, os.fdopen(dst_fd, "wb") as dst_fh:
            shutil.copyfileobj(src_fh, dst_fh, length=1024 * 1024)
    finally:
        if src_handle is not None:
            _close_windows_handle(src_handle)
        if dst_handle is not None:
            _close_windows_handle(dst_handle)


def delete_file_with_windows_handle(src: Path, allowed: list[str], context: dict[str, Any] | None = None) -> None:
    handle = _open_windows_file_handle(
        src,
        access=_win_delete_access() | _win_file_read_attributes(),
        creation=_win_open_existing(),
    )
    try:
        _assert_windows_handle_authorized(handle, allowed, context)
        _delete_windows_handle_on_close(handle)
    finally:
        _close_windows_handle(handle)


def _assert_windows_handle_authorized(handle: int, allowed: list[str], context: dict[str, Any] | None) -> None:
    _assert_windows_handle_not_reparse_point(handle)
    final_path = _windows_final_path(handle)
    real_target = Path(final_path).expanduser().resolve(strict=False)
    scope = _explicit_scope(context) if context else None
    base = authorized_real_base(real_target, allowed, explicit_scope_text=scope)
    reject_reparse_points(base, real_target)


def _open_windows_file_handle(path: Path, *, access: int, creation: int) -> int:
    import ctypes
    from ctypes import wintypes

    parent_handle = _open_windows_directory_handle(path.parent)
    try:
        _assert_windows_handle_not_reparse_point(parent_handle)
        handle = _kernel32().CreateFileW(
            str(path),
            access,
            _win_share_read() | _win_share_write() | _win_share_delete(),
            None,
            creation,
            _win_file_attribute_normal() | _win_file_flag_open_reparse_point(),
            None,
        )
    finally:
        _close_windows_handle(parent_handle)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed for {path}")
    return int(handle)


def _open_windows_directory_handle(path: Path) -> int:
    import ctypes
    from ctypes import wintypes

    handle = _kernel32().CreateFileW(
        str(path),
        _win_file_read_attributes(),
        _win_share_read() | _win_share_write() | _win_share_delete(),
        None,
        _win_open_existing(),
        _win_file_flag_backup_semantics() | _win_file_flag_open_reparse_point(),
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed for directory {path}")
    return int(handle)


def _windows_final_path(handle: int) -> str:
    import ctypes

    buffer_len = 32768
    buffer = ctypes.create_unicode_buffer(buffer_len)
    result = _kernel32().GetFinalPathNameByHandleW(handle, buffer, buffer_len, 0)
    if result == 0 or result >= buffer_len:
        raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
    path = buffer.value
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _close_windows_handle(handle: int) -> None:
    _kernel32().CloseHandle(handle)


def _assert_windows_handle_not_reparse_point(handle: int) -> None:
    attributes = _windows_file_attributes(handle)
    if attributes & _win_file_attribute_reparse_point():
        raise SecurityError("Filesystem links inside authorized directories are not writable.")


def _windows_file_attributes(handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    info = FileAttributeTagInfo()
    ok = _kernel32().GetFileInformationByHandleEx(
        handle,
        _win_file_attribute_tag_info(),
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandleEx(FileAttributeTagInfo) failed")
    return int(info.FileAttributes)


def _delete_windows_handle_on_close(handle: int) -> None:
    import ctypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", ctypes.c_byte)]

    info = FileDispositionInfo(1)
    ok = _kernel32().SetFileInformationByHandle(
        handle,
        _win_file_disposition_info(),
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "SetFileInformationByHandle(FileDispositionInfo) failed")


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _win_generic_read() -> int:
    return 0x80000000


def _win_generic_write() -> int:
    return 0x40000000


def _win_delete_access() -> int:
    return 0x00010000


def _win_file_read_attributes() -> int:
    return 0x00000080


def _win_share_read() -> int:
    return 0x00000001


def _win_share_write() -> int:
    return 0x00000002


def _win_share_delete() -> int:
    return 0x00000004


def _win_open_existing() -> int:
    return 3


def _win_create_always() -> int:
    return 2


def _win_file_attribute_normal() -> int:
    return 0x00000080


def _win_file_attribute_reparse_point() -> int:
    return 0x00000400


def _win_file_flag_open_reparse_point() -> int:
    return 0x00200000


def _win_file_flag_backup_semantics() -> int:
    return 0x02000000


def _win_file_attribute_tag_info() -> int:
    return 9


def _win_file_disposition_info() -> int:
    return 4


def _explicit_scope(context: dict[str, Any] | None) -> str | None:
    value = (context or {}).get("explicit_path_scope")
    if isinstance(value, str) and value.strip():
        return value
    return None
