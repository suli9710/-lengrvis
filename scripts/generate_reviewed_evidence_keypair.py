#!/usr/bin/env python3
"""Generate an Ed25519 keypair for reviewed release-evidence sealing."""

from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TypeAlias

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

FileIdentity: TypeAlias = tuple[int, int, int]


def generate_keypair() -> tuple[str, str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    private_text = "ed25519:" + _b64url(private_raw)
    public_text = "ed25519:" + _b64url(public_raw)
    fingerprint = hashlib.sha256(public_raw).hexdigest()
    return private_text, public_text, fingerprint


def write_keypair(*, private_key_path: Path, public_key_path: Path) -> str:
    private_path = private_key_path.expanduser().resolve(strict=False)
    public_path = public_key_path.expanduser().resolve(strict=False)
    if private_path == public_path:
        raise ValueError("private and public key output paths must be different")
    for path in (private_path, public_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing key file: {path}")

    private_text, public_text, fingerprint = generate_keypair()
    private_identity = _exclusive_write(
        private_path,
        private_text,
        protect_private=True,
    )
    try:
        _exclusive_write(public_path, public_text)
    except Exception:
        _unlink_if_same_file(private_path, private_identity)
        raise
    return fingerprint


def _exclusive_write(
    path: Path,
    text: str,
    *,
    protect_private: bool = False,
) -> FileIdentity:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = _open_exclusive_file(
        path,
        protect_private=protect_private,
    )
    identity: FileIdentity | None = None
    try:
        identity = _file_identity_from_descriptor(descriptor)
        if protect_private and os.name == "nt":
            _protect_windows_private_key_file(
                path,
                descriptor=descriptor,
                expected_identity=identity,
                repair=False,
            )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            _chmod_open_file(handle.fileno(), path=path)
            if protect_private and os.name == "nt":
                _protect_windows_private_key_file(
                    path,
                    descriptor=handle.fileno(),
                    expected_identity=identity,
                    repair=True,
                )
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if identity is not None:
            _unlink_if_same_file(path, identity)
        raise
    assert identity is not None
    return identity


def _open_exclusive_file(path: Path, *, protect_private: bool) -> int:
    if protect_private and os.name == "nt":
        return _open_windows_private_key_file(path)
    return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)


def _open_windows_private_key_file(path: Path) -> int:
    """Create a private file with its restrictive DACL applied atomically."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("security_descriptor", wintypes.LPVOID),
            ("inherit_handle", wintypes.BOOL),
        ]

    security_descriptor = wintypes.LPVOID()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    convert_descriptor = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert_descriptor.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert_descriptor.restype = wintypes.BOOL
    current_user_sid = _current_windows_user_sid()
    sddl = f"O:{current_user_sid}D:P(A;;FA;;;{current_user_sid})"
    if not convert_descriptor(sddl, 1, ctypes.byref(security_descriptor), None):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to build the private-key security descriptor")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    attributes = _SecurityAttributes(
        length=ctypes.sizeof(_SecurityAttributes),
        security_descriptor=security_descriptor,
        inherit_handle=False,
    )
    handle = wintypes.HANDLE(-1)
    try:
        handle = create_file(
            _windows_extended_path(path),
            0x40000000,  # GENERIC_WRITE
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            ctypes.byref(attributes),
            1,  # CREATE_NEW
            0x00000080,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            error = ctypes.get_last_error()
            if error in (80, 183):
                raise FileExistsError(
                    errno.EEXIST,
                    os.strerror(errno.EEXIST),
                    str(path),
                )
            raise OSError(error, "unable to create the private-key file", str(path))
        try:
            return msvcrt.open_osfhandle(
                int(handle),
                os.O_WRONLY | getattr(os, "O_BINARY", 0),
            )
        except Exception:
            kernel32.CloseHandle(handle)
            raise
    finally:
        local_free(security_descriptor)


def _current_windows_user_sid() -> str:
    import ctypes
    from ctypes import wintypes

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", wintypes.LPVOID), ("attributes", wintypes.DWORD)]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("user", _SidAndAttributes)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    open_token = advapi32.OpenProcessToken
    open_token.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    open_token.restype = wintypes.BOOL
    if not open_token(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to open the current process token")
    try:
        get_token_information = advapi32.GetTokenInformation
        get_token_information.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        get_token_information.restype = wintypes.BOOL
        required = wintypes.DWORD()
        get_token_information(token, 1, None, 0, ctypes.byref(required))
        if ctypes.get_last_error() != 122 or required.value == 0:
            error = ctypes.get_last_error()
            raise OSError(error, "unable to size the current-user token information")
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(
            token,
            1,
            buffer,
            required,
            ctypes.byref(required),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, "unable to read the current-user token information")
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        sid_text = wintypes.LPWSTR()
        convert_sid = advapi32.ConvertSidToStringSidW
        convert_sid.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        convert_sid.restype = wintypes.BOOL
        if not convert_sid(token_user.user.sid, ctypes.byref(sid_text)):
            error = ctypes.get_last_error()
            raise OSError(error, "unable to encode the current-user SID")
        try:
            return str(sid_text.value)
        finally:
            kernel32.LocalFree(sid_text)
    finally:
        kernel32.CloseHandle(token)


def _windows_extended_path(path: Path) -> str:
    text = str(path)
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _protect_windows_private_key_file(
    path: Path,
    *,
    descriptor: int,
    expected_identity: FileIdentity,
    repair: bool,
) -> None:
    """Apply a current-user-only DACL to the file held by ``descriptor``."""

    if _file_identity_from_descriptor(descriptor) != expected_identity:
        raise OSError("private-key file identity changed before DACL protection")

    script = r"""
$ErrorActionPreference = 'Stop'
$path = [IO.Path]::GetFullPath($env:LENGRVIS_REVIEWED_EVIDENCE_ACL_TARGET)
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$icacls = [IO.Path]::Combine($env:SystemRoot, 'System32', 'icacls.exe')
if (-not [IO.File]::Exists($icacls)) { throw 'the trusted System32 icacls.exe was not found' }
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;

public static class LengrvisReviewedEvidenceFileIdentity
{
    [StructLayout(LayoutKind.Sequential)]
    private struct NativeFileTime
    {
        public uint Low;
        public uint High;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ByHandleFileInformation
    {
        public uint FileAttributes;
        public NativeFileTime CreationTime;
        public NativeFileTime LastAccessTime;
        public NativeFileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        IntPtr handle,
        out ByHandleFileInformation information
    );

    public static string Read(string path)
    {
        using (FileStream stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite
        ))
        {
            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(
                stream.SafeFileHandle.DangerousGetHandle(),
                out information
            ))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return String.Format(
                "{0:x8}:{1:x8}:{2:x8}",
                information.VolumeSerialNumber,
                information.FileIndexHigh,
                information.FileIndexLow
            );
        }
    }
}
'@
$expectedIdentity = $env:LENGRVIS_REVIEWED_EVIDENCE_FILE_ID
if ([LengrvisReviewedEvidenceFileIdentity]::Read($path) -cne $expectedIdentity) {
    throw 'private-key path identity changed before DACL protection'
}
if ($env:LENGRVIS_REVIEWED_EVIDENCE_REPAIR_DACL -ceq '1') {
    & $icacls $path /inheritance:r /grant:r "*$($sid.Value):(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'icacls failed to apply the private-key DACL' }
}
if ([LengrvisReviewedEvidenceFileIdentity]::Read($path) -cne $expectedIdentity) {
    throw 'private-key path identity changed while applying the DACL'
}
$acl = [IO.File]::GetAccessControl($path)
$rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
$allows = @($rules | Where-Object {
    $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow
})
$owner = $acl.GetOwner([Security.Principal.SecurityIdentifier])
if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 1 -or $allows.Count -ne 1) {
    throw 'private-key DACL is inherited or contains another access rule'
}
if ($allows[0].IdentityReference.Value -ne $sid.Value) {
    throw 'private-key DACL does not grant the current user access'
}
if ($owner.Value -ne $sid.Value) {
    throw 'private-key file owner is not the current user'
}
$full = [Security.AccessControl.FileSystemRights]::FullControl
if (($allows[0].FileSystemRights -band $full) -ne $full) {
    throw 'private-key DACL does not grant the current user full control'
}
"""
    system_root = _trusted_windows_directory()
    powershell = (
        system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    if not powershell.is_file():
        raise OSError("the trusted Windows PowerShell executable was not found")
    try:
        completed = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=_powershell_environment(
                system_root=system_root,
                path=path,
                expected_identity=expected_identity,
                repair=repair,
            ),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("unable to apply the Windows private-key DACL") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise OSError(
            "Windows private-key DACL verification failed"
            + (f": {detail}" if detail else "")
        )
    if _file_identity_from_descriptor(descriptor) != expected_identity:
        raise OSError("private-key file identity changed after DACL protection")


def _file_identity_from_descriptor(descriptor: int) -> FileIdentity:
    if os.name != "nt":
        stat = os.fstat(descriptor)
        return int(stat.st_dev), int(stat.st_ino), 0

    import ctypes
    import msvcrt

    class _NativeFileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", ctypes.c_uint32),
            ("creation_time", _NativeFileTime),
            ("last_access_time", _NativeFileTime),
            ("last_write_time", _NativeFileTime),
            ("volume_serial_number", ctypes.c_uint32),
            ("file_size_high", ctypes.c_uint32),
            ("file_size_low", ctypes.c_uint32),
            ("number_of_links", ctypes.c_uint32),
            ("file_index_high", ctypes.c_uint32),
            ("file_index_low", ctypes.c_uint32),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    get_information.restype = ctypes.c_int
    information = _ByHandleFileInformation()
    handle = msvcrt.get_osfhandle(descriptor)
    if handle == -1 or not get_information(
        ctypes.c_void_p(handle), ctypes.byref(information)
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "unable to read the private-key file identity")
    return (
        int(information.volume_serial_number),
        int(information.file_index_high),
        int(information.file_index_low),
    )


def _file_identity_text(identity: FileIdentity) -> str:
    return ":".join(f"{part:08x}" for part in identity)


def _trusted_windows_directory() -> Path:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_windows_directory = kernel32.GetWindowsDirectoryW
    get_windows_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_windows_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(260)
    length = get_windows_directory(buffer, len(buffer))
    if length == 0:
        error = ctypes.get_last_error()
        raise OSError(error, "unable to resolve the trusted Windows directory")
    if length >= len(buffer):
        buffer = ctypes.create_unicode_buffer(length + 1)
        length = get_windows_directory(buffer, len(buffer))
        if length == 0 or length >= len(buffer):
            error = ctypes.get_last_error()
            raise OSError(error, "unable to resolve the trusted Windows directory")
    return Path(buffer.value)


def _powershell_environment(
    *,
    system_root: Path,
    path: Path,
    expected_identity: FileIdentity,
    repair: bool,
) -> dict[str, str]:
    temp_directory = tempfile.gettempdir()
    system32 = system_root / "System32"
    return {
        "SystemRoot": str(system_root),
        "WINDIR": str(system_root),
        "SystemDrive": system_root.drive,
        "ComSpec": str(system32 / "cmd.exe"),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "PATH": os.pathsep.join(
            (
                str(system32),
                str(system_root / "System32" / "WindowsPowerShell" / "v1.0"),
            )
        ),
        "TEMP": temp_directory,
        "TMP": temp_directory,
        "LENGRVIS_REVIEWED_EVIDENCE_ACL_TARGET": str(path),
        "LENGRVIS_REVIEWED_EVIDENCE_FILE_ID": _file_identity_text(expected_identity),
        "LENGRVIS_REVIEWED_EVIDENCE_REPAIR_DACL": "1" if repair else "0",
    }


def _chmod_open_file(descriptor: int, *, path: Path) -> None:
    if os.name == "nt":
        return
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    else:
        os.chmod(path, 0o600)


def _unlink_if_same_file(path: Path, expected_identity: FileIdentity) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return
    except OSError:
        return
    try:
        if _file_identity_from_descriptor(descriptor) != expected_identity:
            return
    finally:
        os.close(descriptor)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-key-output", required=True)
    parser.add_argument("--public-key-output", required=True)
    args = parser.parse_args(argv)
    try:
        fingerprint = write_keypair(
            private_key_path=Path(args.private_key_output),
            public_key_path=Path(args.public_key_output),
        )
    except (OSError, ValueError) as exc:
        print(f"reviewed-evidence-keypair: generation blocked: {exc}", file=sys.stderr)
        return 1
    print(f"reviewed-evidence-keypair: generated public key sha256:{fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
