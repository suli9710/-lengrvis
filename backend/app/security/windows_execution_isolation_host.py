"""Adapter for an installed, signed Windows execution-isolation host.

This module is intentionally only an adapter.  It never claims to implement
AppContainer, restricted-token, filesystem, or network-broker enforcement in
Python.  A separately built native host must enforce those controls and return
the detached Ed25519 attestation verified by :mod:`execution_isolation`.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.process_tree import run_process_tree
from app.core.windows_job import WindowsJobLimits

HOST_PATH_ENV = "LENGRVIS_WINDOWS_ISOLATION_HOST_PATH"
HOST_SHA256_ENV = "LENGRVIS_WINDOWS_ISOLATION_HOST_SHA256"
POLICY_PATH_ENV = "LENGRVIS_WINDOWS_ISOLATION_POLICY_PATH"
POLICY_SHA256_ENV = "LENGRVIS_WINDOWS_ISOLATION_POLICY_SHA256"
ATTESTATION_REQUEST_SCHEMA = "lengrvis-windows-execution-isolation-request-v1"
MAX_ATTESTATION_RESPONSE_BYTES = 64 * 1024
HOST_TIMEOUT_SECONDS = 5.0
_SHA256_PREFIX = "sha256:"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400


def attest_current_process_tree(challenge: Mapping[str, Any]) -> dict[str, Any]:
    """Ask the installed native host to attest the current backend process tree."""

    if sys.platform != "win32":
        raise RuntimeError("The Windows isolation host adapter is unavailable on this platform.")
    if not isinstance(challenge, Mapping):
        raise RuntimeError("The Windows isolation challenge is invalid.")
    host_path = _trusted_regular_file(HOST_PATH_ENV, suffix=".exe")
    policy_path = _trusted_regular_file(POLICY_PATH_ENV)
    expected_host_digest = _configured_digest(HOST_SHA256_ENV)
    expected_policy_digest = _configured_digest(POLICY_SHA256_ENV)
    validated_challenge = _validated_challenge(
        challenge,
        expected_host_digest=expected_host_digest,
        expected_policy_digest=expected_policy_digest,
    )
    _assert_digest(host_path, expected_host_digest, label="host binary")
    _assert_digest(policy_path, expected_policy_digest, label="policy")
    if not _authenticode_signature_valid(host_path):
        raise RuntimeError("The Windows isolation host does not have a trusted embedded Authenticode signature.")

    request = {
        "schema_version": ATTESTATION_REQUEST_SCHEMA,
        "challenge": validated_challenge,
        "expected_host_binary_sha256": expected_host_digest,
        "expected_policy_sha256": expected_policy_digest,
    }
    encoded_request = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    completed = run_process_tree(
        [
            str(host_path),
            "--attest-current-process-tree",
            "--policy",
            str(policy_path),
        ],
        input=encoded_request + "\n",
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=HOST_TIMEOUT_SECONDS,
        hide_window=True,
        cwd=str(host_path.parent),
        env=_minimal_host_environment(),
        windows_job_limits=WindowsJobLimits(
            active_processes=1,
            process_memory_bytes=256 * 1024 * 1024,
            job_memory_bytes=256 * 1024 * 1024,
        ),
        require_windows_isolation=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("The Windows isolation host rejected the attestation request.")
    stdout = str(completed.stdout or "")
    if len(stdout.encode("utf-8")) > MAX_ATTESTATION_RESPONSE_BYTES:
        raise RuntimeError("The Windows isolation host response exceeded the size limit.")
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("The Windows isolation host must return exactly one JSON line.")
    try:
        response = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("The Windows isolation host returned invalid JSON.") from exc
    if not isinstance(response, dict) or set(response) != {"payload", "signature"}:
        raise RuntimeError("The Windows isolation host returned an invalid response envelope.")

    # Re-check both artifacts after execution.  The signed payload is still the
    # authoritative enforcement claim, while these pins detect ordinary file
    # replacement races and bind the installed artifacts to the release.
    _assert_digest(host_path, expected_host_digest, label="host binary")
    _assert_digest(policy_path, expected_policy_digest, label="policy")
    return response


def _trusted_regular_file(environment_name: str, *, suffix: str = "") -> Path:
    raw = str(os.environ.get(environment_name) or "").strip()
    candidate = Path(raw)
    if not raw or not candidate.is_absolute():
        raise RuntimeError(f"{environment_name} must name an absolute file path.")
    try:
        _assert_no_reparse_points(candidate)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{environment_name} could not be resolved.") from exc
    if not resolved.is_file():
        raise RuntimeError(f"{environment_name} must name a regular file.")
    if suffix and resolved.suffix.casefold() != suffix.casefold():
        raise RuntimeError(f"{environment_name} must name a {suffix} file.")
    return resolved


def _assert_no_reparse_points(path: Path) -> None:
    chain = list(reversed(path.parents)) + [path]
    for component in chain:
        attributes = int(getattr(os.lstat(component), "st_file_attributes", 0) or 0)
        if component.is_symlink() or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise RuntimeError("Windows isolation artifact paths must not contain reparse points.")


def _configured_digest(environment_name: str) -> str:
    value = str(os.environ.get(environment_name) or "").strip().casefold()
    if not value.startswith(_SHA256_PREFIX) or len(value) != len(_SHA256_PREFIX) + 64:
        raise RuntimeError(f"{environment_name} is missing or invalid.")
    try:
        bytes.fromhex(value.removeprefix(_SHA256_PREFIX))
    except ValueError as exc:
        raise RuntimeError(f"{environment_name} is missing or invalid.") from exc
    return value


def _validated_challenge(
    challenge: Mapping[str, Any],
    *,
    expected_host_digest: str,
    expected_policy_digest: str,
) -> dict[str, Any]:
    required = {
        "nonce",
        "process_id",
        "parent_process_id",
        "expected_host_binary_sha256",
        "expected_policy_sha256",
    }
    if set(challenge) != required:
        raise RuntimeError("The Windows isolation challenge shape is invalid.")
    nonce = challenge.get("nonce")
    process_id = challenge.get("process_id")
    parent_process_id = challenge.get("parent_process_id")
    if (
        not isinstance(nonce, str)
        or not 32 <= len(nonce) <= 256
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in nonce)
    ):
        raise RuntimeError("The Windows isolation challenge nonce is invalid.")
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise RuntimeError("The Windows isolation challenge process id is invalid.")
    if isinstance(parent_process_id, bool) or not isinstance(parent_process_id, int) or parent_process_id < 0:
        raise RuntimeError("The Windows isolation challenge parent process id is invalid.")
    if challenge.get("expected_host_binary_sha256") != expected_host_digest:
        raise RuntimeError("The Windows isolation challenge host digest did not match the release pin.")
    if challenge.get("expected_policy_sha256") != expected_policy_digest:
        raise RuntimeError("The Windows isolation challenge policy digest did not match the release pin.")
    return dict(challenge)


def _assert_digest(path: Path, expected: str, *, label: str) -> None:
    actual = _file_sha256(path)
    if actual != expected:
        raise RuntimeError(f"The Windows isolation {label} digest did not match the release pin.")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise RuntimeError("The Windows isolation artifact could not be read.") from exc
    return f"sha256:{digest.hexdigest()}"


def _minimal_host_environment() -> dict[str, str]:
    allowed = {
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed and "\x00" not in value}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _WinTrustFileInfo(ctypes.Structure):
    _fields_ = [
        ("cbStruct", ctypes.c_uint32),
        ("pcwszFilePath", ctypes.c_wchar_p),
        ("hFile", ctypes.c_void_p),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class _WinTrustData(ctypes.Structure):
    _fields_ = [
        ("cbStruct", ctypes.c_uint32),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", ctypes.c_uint32),
        ("fdwRevocationChecks", ctypes.c_uint32),
        ("dwUnionChoice", ctypes.c_uint32),
        ("pFile", ctypes.POINTER(_WinTrustFileInfo)),
        ("dwStateAction", ctypes.c_uint32),
        ("hWVTStateData", ctypes.c_void_p),
        ("pwszURLReference", ctypes.c_wchar_p),
        ("dwProvFlags", ctypes.c_uint32),
        ("dwUIContext", ctypes.c_uint32),
        ("pSignatureSettings", ctypes.c_void_p),
    ]


def _authenticode_signature_valid(path: Path) -> bool:
    if sys.platform != "win32":
        return False
    action = _GUID(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    file_info = _WinTrustFileInfo(
        ctypes.sizeof(_WinTrustFileInfo),
        str(path),
        None,
        None,
    )
    trust_data = _WinTrustData()
    trust_data.cbStruct = ctypes.sizeof(_WinTrustData)
    trust_data.dwUIChoice = 2  # WTD_UI_NONE
    trust_data.fdwRevocationChecks = 1  # WTD_REVOKE_WHOLECHAIN
    trust_data.dwUnionChoice = 1  # WTD_CHOICE_FILE
    trust_data.pFile = ctypes.pointer(file_info)
    trust_data.dwStateAction = 0  # WTD_STATEACTION_IGNORE
    trust_data.dwProvFlags = 0x00000080  # WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT
    try:
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        verify = wintrust.WinVerifyTrust
        verify.argtypes = [ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(_WinTrustData)]
        verify.restype = ctypes.c_long
        return int(verify(None, ctypes.byref(action), ctypes.byref(trust_data))) == 0
    except (AttributeError, OSError, TypeError, ValueError):
        return False
