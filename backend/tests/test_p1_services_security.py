"""Regression tests for P1-14/P1-15/P1-16 service-layer security fixes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core import db
from app.core.errors import SecurityError
from app.security.sensitive_confirmation import create_settings_confirmation
from app.services import document_intelligence_service as doc_svc
from app.services import file_service, guardian_runtime


def test_add_directory_rejects_system_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()

    with pytest.raises(SecurityError, match="Sensitive or system paths"):
        file_service.add_directory(
            "C:/Windows",
            confirmation_nonce=create_settings_confirmation({"allowed_directories": ["C:/Windows"]})["nonce"],
        )


def test_add_directory_rejects_sensitive_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    sensitive_dir = tmp_path / ".ssh"
    sensitive_dir.mkdir()

    with pytest.raises(SecurityError, match="Sensitive or system paths"):
        file_service.add_directory(
            str(sensitive_dir),
            confirmation_nonce=create_settings_confirmation({"allowed_directories": [str(sensitive_dir)]})["nonce"],
        )


def test_add_directory_stores_resolved_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias_path = workspace / "."
    resolved = str(workspace.resolve())
    nonce = create_settings_confirmation({"allowed_directories": [resolved]})["nonce"]

    result = file_service.add_directory(str(alias_path), confirmation_nonce=nonce)

    assert result["allowed_directories"] == [resolved]


def test_parse_advanced_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "large.txt"
    path.write_bytes(b"x" * 32)
    monkeypatch.setenv("LENGRVIS_DOCUMENT_MAX_PARSE_BYTES", "16")

    with pytest.raises(doc_svc.DocumentTooLargeError, match="parse size limit"):
        doc_svc.parse_advanced(path)


def test_parse_advanced_allows_file_under_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "memo.txt"
    path.write_text("Executive summary", encoding="utf-8")

    ir = doc_svc.parse_advanced(path)

    assert ir.parse_engine == "builtin"
    assert "Executive summary" in ir.text


def test_full_backend_command_default_does_not_require_custom_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LENGRVIS_FULL_BACKEND_COMMAND", raising=False)
    runtime = guardian_runtime.GuardianRuntime()

    command = runtime._full_backend_command()

    assert command[0] == sys.executable
    assert "uvicorn" in command


def test_full_backend_command_allows_sys_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    monkeypatch.setenv("LENGRVIS_FULL_BACKEND_COMMAND", f'"{sys.executable}" -m uvicorn backend.main:full_app')
    runtime = guardian_runtime.GuardianRuntime()

    command = runtime._full_backend_command()

    assert command[0] == sys.executable
    events = db.fetch_many("audit_events", limit=5)
    assert any(event.get("event_type") == "guardian.full_backend_command" for event in events)


def test_full_backend_command_rejects_shell_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LENGRVIS_FULL_BACKEND_COMMAND", "cmd.exe /c python -m uvicorn backend.main:full_app")
    runtime = guardian_runtime.GuardianRuntime()

    with pytest.raises(RuntimeError, match="Shell interpreters are not allowed"):
        runtime._full_backend_command()


def test_full_backend_command_rejects_disallowed_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    blocked = tmp_path / "blocked-backend.exe"
    blocked.write_bytes(b"MZ")
    monkeypatch.setenv("LENGRVIS_FULL_BACKEND_COMMAND", f'"{blocked}" --serve')
    runtime = guardian_runtime.GuardianRuntime()

    with pytest.raises(RuntimeError, match="executable is not allowed"):
        runtime._full_backend_command()


def test_full_backend_command_honors_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "custom-backend.exe"
    allowed.write_bytes(b"MZ")
    monkeypatch.setenv("LENGRVIS_FULL_BACKEND_COMMAND", f'"{allowed}" --serve')
    monkeypatch.setenv("LENGRVIS_FULL_BACKEND_COMMAND_ALLOWLIST", str(allowed))
    runtime = guardian_runtime.GuardianRuntime()

    command = runtime._full_backend_command()

    assert command[0] == str(allowed)
