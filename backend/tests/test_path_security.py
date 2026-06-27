from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import pytest

from app.core.errors import SecurityError
from app.core.paths import is_system_path, resolve_authorized


def _resolve(root: Path, candidate: str):
    decoded = unquote(candidate).replace("\\", "/")
    return resolve_authorized(root / decoded, [str(root)])


def test_path_security_uses_real_app_contract():
    assert resolve_authorized.__module__ == "app.core.paths"


@pytest.mark.parametrize(
    "candidate",
    [
        "../outside.txt",
        "..\\outside.txt",
        "notes/../../outside.txt",
        "/etc/passwd",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "notes/safe.txt:stream",
        "notes/safe.txt:stream:$DATA",
        "notes/%2e%2e/outside.txt",
    ],
)
def test_rejects_paths_that_escape_workspace(workspace: Path, candidate: str):
    with pytest.raises(SecurityError):
        _resolve(workspace, candidate)


@pytest.mark.parametrize(
    "candidate",
    [
        "\\\\localhost\\C$\\Windows\\system.ini",
        "\\\\?\\C:\\Windows\\win.ini",
        "\\\\.\\NUL",
    ],
)
def test_rejects_windows_namespace_paths(workspace: Path, candidate: str):
    with pytest.raises(SecurityError):
        _resolve(workspace, candidate)


def test_allows_normalized_child_path(workspace: Path):
    resolved = Path(_resolve(workspace, "notes/./safe.txt")).resolve()

    assert resolved == (workspace / "notes" / "safe.txt").resolve()
    assert resolved.is_relative_to(workspace.resolve())


@pytest.mark.parametrize(
    "candidate",
    [
        ".env",
        ".env.local",
        ".npmrc",
        ".netrc",
        ".docker/config.json",
        ".kube/config",
        ".aws/sso/cache/token.json",
        "AppData/Local/Microsoft/Edge/User Data/Default/Login Data",
        "AppData/Roaming/Mozilla/Firefox/Profiles/abc.default-release/key4.db",
        "AppData/Roaming/Mozilla/Firefox/Profiles/abc.default-release/logins.json",
        "certs/client.pem",
    ],
)
def test_rejects_sensitive_credential_paths_inside_workspace(workspace: Path, candidate: str):
    with pytest.raises(SecurityError):
        _resolve(workspace, candidate)


@pytest.mark.parametrize(
    "candidate",
    [
        Path("D:/Windows/System32/drivers/etc/hosts"),
        Path("E:/Program Files/App/app.exe"),
        Path("F:/Program Files (x86)/App/app.exe"),
        Path("G:/ProgramData/Microsoft/Crypto/key"),
    ],
)
def test_windows_system_paths_are_drive_agnostic(candidate: Path):
    assert is_system_path(candidate) is True


def test_rejects_symlink_escape(workspace: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not read\n", encoding="utf-8")

    link = workspace / "linked-outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")

    with pytest.raises(SecurityError):
        _resolve(workspace, "linked-outside/secret.txt")
