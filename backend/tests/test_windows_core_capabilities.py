from __future__ import annotations

import builtins
import http.server
import json
import socketserver
import sys
import threading
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import Approval, ApprovalStatus
from app.main import create_app
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionStore
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel
from app.services import system_service
from app.tools import app_tools, browser_tools, search_tools, system_tools, ui_automation_tools
from app.tools.tool_abort import ToolAbortedError


def _init_test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    db.init_db()


def _settings_context():
    from app.llm.registry import get_effective_settings

    settings = get_effective_settings()
    return {"settings": settings, "allowed_directories": settings.allowed_directories}


def test_app_list_and_allowlisted_launch_dry_run(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_APP_ALLOWLIST="notepad;calc")
    context = _settings_context()

    apps = app_tools.list_installed({}, context)
    launch = app_tools.launch_installed({"app": "notepad", "dry_run": True}, context)

    assert any(app["id"] == "notepad" for app in apps["apps"])
    assert launch == {"ok": True, "dry_run": True, "command": "notepad.exe"}


def test_app_launch_unknown_application_is_blocked(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_APP_ALLOWLIST="notepad")
    result = app_tools.launch_installed({"app": "unknown-app", "dry_run": True}, _settings_context())

    assert result["ok"] is False
    assert "allowlisted" in result["error"]


def test_uninstall_app_rejects_direct_uninstall_command(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)

    result = app_tools.uninstall_app(
        {"query": "anything", "uninstall_string": "powershell -NoProfile -Command calc.exe", "dry_run": True},
        _settings_context(),
    )

    assert result["ok"] is False
    assert "Direct uninstall commands" in result["error"]


def test_appx_scan_decodes_utf16_json_without_bom(monkeypatch):
    monkeypatch.setattr(app_tools.platform, "system", lambda: "Windows")

    class Completed:
        returncode = 0
        stdout = (
            '[{"Name":"Sample.App","PackageFullName":"Sample.App_1.0.0.0_x64__abc",'
            '"Publisher":"CN=Vendor","Version":"1.0.0.0","InstallLocation":"C:\\\\Sample"}]'
        ).encode("utf-16le")

    monkeypatch.setattr(app_tools.subprocess, "run", lambda *args, **kwargs: Completed())

    apps = app_tools._scan_appx_packages()

    assert apps[0]["id"] == "sample.app"
    assert apps[0]["package_full_name"] == "Sample.App_1.0.0.0_x64__abc"
    assert apps[0]["source"] == "appx"


def test_winget_scan_decodes_utf16_json_without_bom(monkeypatch):
    class Completed:
        returncode = 0
        stdout = (
            '{"Packages":[{"Id":"Vendor.Sample","Name":"Sample App",'
            '"Publisher":"Vendor","Version":"2.0","Source":"winget"}]}'
        ).encode("utf-16be")

    monkeypatch.setattr(app_tools.subprocess, "run", lambda *args, **kwargs: Completed())

    apps = app_tools._scan_winget_packages()

    assert apps[0]["id"] == "vendor.sample"
    assert apps[0]["name"] == "Sample App"
    assert apps[0]["source"] == "winget"


def test_registry_scan_degrades_when_winreg_is_unavailable(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, global_vars=None, local_vars=None, fromlist=(), level=0):
        if name == "winreg":
            raise ImportError("winreg unavailable")
        return real_import(name, global_vars, local_vars, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert app_tools._scan_registry_apps() == []


def test_registry_scan_does_not_swallow_unexpected_winreg_bugs(monkeypatch):
    fake_winreg = types.ModuleType("winreg")
    fake_winreg.HKEY_CURRENT_USER = object()
    fake_winreg.HKEY_LOCAL_MACHINE = object()

    def broken_open_key(*_args):
        raise RuntimeError("registry bug")

    fake_winreg.OpenKey = broken_open_key
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    with pytest.raises(RuntimeError, match="registry bug"):
        app_tools._scan_registry_apps()


def test_appx_scan_degrades_for_expected_process_and_json_failures(monkeypatch):
    monkeypatch.setattr(app_tools.platform, "system", lambda: "Windows")

    class InvalidJsonCompleted:
        returncode = 0
        stdout = b"{"

    monkeypatch.setattr(app_tools.subprocess, "run", lambda *args, **kwargs: InvalidJsonCompleted())
    assert app_tools._scan_appx_packages() == []

    def raise_timeout(*_args, **_kwargs):
        raise app_tools.subprocess.TimeoutExpired(cmd="powershell.exe", timeout=1)

    monkeypatch.setattr(app_tools.subprocess, "run", raise_timeout)
    assert app_tools._scan_appx_packages() == []

    def raise_os_error(*_args, **_kwargs):
        raise PermissionError("powershell blocked")

    monkeypatch.setattr(app_tools.subprocess, "run", raise_os_error)
    assert app_tools._scan_appx_packages() == []


def test_appx_scan_does_not_swallow_unexpected_process_bugs(monkeypatch):
    monkeypatch.setattr(app_tools.platform, "system", lambda: "Windows")

    def raise_bug(*_args, **_kwargs):
        raise RuntimeError("appx bug")

    monkeypatch.setattr(app_tools.subprocess, "run", raise_bug)

    with pytest.raises(RuntimeError, match="appx bug"):
        app_tools._scan_appx_packages()


def test_winget_scan_degrades_for_expected_process_and_json_failures(monkeypatch):
    class InvalidJsonCompleted:
        returncode = 0
        stdout = b"{"

    monkeypatch.setattr(app_tools.subprocess, "run", lambda *args, **kwargs: InvalidJsonCompleted())
    assert app_tools._scan_winget_packages() == []

    def raise_timeout(*_args, **_kwargs):
        raise app_tools.subprocess.TimeoutExpired(cmd="winget", timeout=1)

    monkeypatch.setattr(app_tools.subprocess, "run", raise_timeout)
    assert app_tools._scan_winget_packages() == []

    def raise_os_error(*_args, **_kwargs):
        raise FileNotFoundError("winget")

    monkeypatch.setattr(app_tools.subprocess, "run", raise_os_error)
    assert app_tools._scan_winget_packages() == []


def test_winget_scan_does_not_swallow_unexpected_process_bugs(monkeypatch):
    def raise_bug(*_args, **_kwargs):
        raise RuntimeError("winget bug")

    monkeypatch.setattr(app_tools.subprocess, "run", raise_bug)

    with pytest.raises(RuntimeError, match="winget bug"):
        app_tools._scan_winget_packages()


def _registry_product_apps():
    return [
        {
            "id": "sample product",
            "name": "Sample Product",
            "publisher": "Vendor",
            "uninstall_string": "MsiExec.exe /I {ABC-123}",
            "source": "registry",
        }
    ]


def test_uninstall_app_executes_scanned_entry_without_shell(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_winget_packages", lambda: [])
    call_count = 0

    def registry_scan():
        nonlocal call_count
        call_count += 1
        return _registry_product_apps() if call_count == 1 else []

    monkeypatch.setattr(app_tools, "_scan_registry_apps", registry_scan)
    runs: list[dict[str, object]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        runs.append({"command": command, **kwargs})

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    monkeypatch.setattr(app_tools.subprocess, "run", fake_run)

    result = app_tools.uninstall_app({"query": "Sample Product", "dry_run": False}, _settings_context())

    assert result["ok"] is True
    assert result["verified_removed"] is True
    assert result["returncode"] == 0
    assert runs[0]["command"] == ["MsiExec.exe", "/X", "{ABC-123}"]


def test_uninstall_app_redacts_process_output(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_winget_packages", lambda: [])
    call_count = 0

    def registry_scan():
        nonlocal call_count
        call_count += 1
        return _registry_product_apps() if call_count == 1 else []

    monkeypatch.setattr(app_tools, "_scan_registry_apps", registry_scan)

    def fake_run(*args, **kwargs):  # noqa: ANN001, ANN002
        class _Completed:
            returncode = 1
            stdout = b"removed C:/Users/Suli/private/app-output.log token=app-stdout-secret-1234567890"
            stderr = b"failed C:/Users/Suli/private/app-error.txt api_key=app-stderr-secret-1234567890"

        return _Completed()

    monkeypatch.setattr(app_tools.subprocess, "run", fake_run)

    result = app_tools.uninstall_app({"query": "Sample Product", "dry_run": False}, _settings_context())
    result_text = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["returncode"] == 1
    assert "app-stdout-secret-1234567890" not in result_text
    assert "app-stderr-secret-1234567890" not in result_text
    assert "C:/Users/Suli/private/app-output.log" not in result_text
    assert "C:/Users/Suli/private/app-error.txt" not in result_text
    assert "app-output.log" not in result_text
    assert "app-error.txt" not in result_text
    assert "[REDACTED]" in result_text


def test_uninstall_app_aborts_before_uninstaller_process(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_winget_packages", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_registry_apps", _registry_product_apps)
    runs: list[object] = []
    abort = threading.Event()
    abort.set()

    monkeypatch.setattr(app_tools.subprocess, "run", lambda *args, **kwargs: runs.append((args, kwargs)))

    with pytest.raises(ToolAbortedError):
        app_tools.uninstall_app(
            {"query": "Sample Product", "dry_run": False},
            {**_settings_context(), "_tool_abort_event": abort},
        )

    assert runs == []


def test_uninstall_app_prefers_quiet_uninstall_string(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_winget_packages", lambda: [])
    call_count = 0

    def registry_scan():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                {
                    "id": "quiet product",
                    "name": "Quiet Product",
                    "publisher": "Vendor",
                    "uninstall_string": "MsiExec.exe /I {ABC-123}",
                    "quiet_uninstall_string": "MsiExec.exe /X {ABC-123} /qn /norestart",
                    "source": "registry",
                }
            ]
        return []

    monkeypatch.setattr(app_tools, "_scan_registry_apps", registry_scan)
    runs: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        runs.append(list(command))

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    monkeypatch.setattr(app_tools.subprocess, "run", fake_run)

    result = app_tools.uninstall_app({"query": "Quiet Product", "dry_run": False}, _settings_context())

    assert result["ok"] is True
    assert result["uninstall_method"] == "quiet_registry"
    assert runs[0] == ["MsiExec.exe", "/X", "{ABC-123}", "/qn", "/norestart"]


def test_uninstall_app_winget_channel(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_registry_apps", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: [])
    call_count = 0

    def winget_scan():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                {
                    "id": "vendor.wingetapp",
                    "name": "Winget App",
                    "winget_id": "Vendor.WingetApp",
                    "source": "winget",
                }
            ]
        return []

    monkeypatch.setattr(app_tools, "_scan_winget_packages", winget_scan)
    runs: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        runs.append(list(command))

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    monkeypatch.setattr(app_tools.subprocess, "run", fake_run)

    result = app_tools.uninstall_app({"query": "Winget App", "dry_run": False}, _settings_context())

    assert result["ok"] is True
    assert result["uninstall_method"] == "winget"
    assert runs[0][:4] == ["winget", "uninstall", "--id", "Vendor.WingetApp"]


def test_uninstall_app_appx_channel(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_registry_apps", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_winget_packages", lambda: [])
    call_count = 0

    def appx_scan():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                {
                    "id": "store app",
                    "name": "Store App",
                    "package_full_name": "Publisher.StoreApp_8wekyb3d8bbwe!App",
                    "source": "appx",
                }
            ]
        return []

    monkeypatch.setattr(app_tools, "_scan_appx_packages", appx_scan)
    runs: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        runs.append(list(command))

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    monkeypatch.setattr(app_tools.subprocess, "run", fake_run)

    result = app_tools.uninstall_app({"query": "Store App", "dry_run": False}, _settings_context())

    assert result["ok"] is True
    assert result["uninstall_method"] == "appx"
    assert "Remove-AppxPackage" in runs[0][-1]
    assert "Publisher.StoreApp_8wekyb3d8bbwe!App" in runs[0][-1]


def test_installed_apps_prefers_registry_uninstall_over_shortcut(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_tools,
        "_scan_shortcuts",
        lambda: [
            {"id": "shared app", "name": "Shared App", "path": "C:\\shortcut.lnk", "source": "start_menu"},
        ],
    )
    monkeypatch.setattr(
        app_tools,
        "_scan_registry_apps",
        lambda: [
            {
                "id": "shared app",
                "name": "Shared App",
                "uninstall_string": "MsiExec.exe /X {ABC-123}",
                "source": "registry",
            }
        ],
    )
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_winget_packages", lambda: [])

    apps = app_tools.installed_apps(_settings_context())
    shared = next(app for app in apps if app["name"] == "Shared App")

    assert shared["source"] == "registry"
    assert shared["uninstall_string"] == "MsiExec.exe /X {ABC-123}"


def test_uninstall_verification_ignores_shortcut_with_same_name(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_tools,
        "_scan_shortcuts",
        lambda: [
            {"id": "shared app", "name": "Shared App", "path": "C:\\shortcut.lnk", "source": "start_menu"},
        ],
    )
    call_count = 0

    def registry_scan():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                {
                    "id": "shared app",
                    "name": "Shared App",
                    "uninstall_string": "MsiExec.exe /X {ABC-123}",
                    "source": "registry",
                }
            ]
        return []

    monkeypatch.setattr(app_tools, "_scan_registry_apps", registry_scan)
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_winget_packages", lambda: [])
    monkeypatch.setattr(app_tools.time, "sleep", lambda _seconds: None)

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    monkeypatch.setattr(app_tools.subprocess, "run", fake_run)

    result = app_tools.uninstall_app({"query": "Shared App", "dry_run": False}, _settings_context())

    assert result["ok"] is True
    assert result["verified_removed"] is True


def test_find_uninstall_entries_includes_appx_and_winget(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_registry_apps", lambda: [])
    monkeypatch.setattr(
        app_tools,
        "_scan_appx_packages",
        lambda: [
            {
                "id": "store app",
                "name": "Store App",
                "package_full_name": "Publisher.StoreApp_8wekyb3d8bbwe!App",
                "source": "appx",
            }
        ],
    )
    monkeypatch.setattr(
        app_tools,
        "_scan_winget_packages",
        lambda: [
            {
                "id": "vendor.wingetapp",
                "name": "Winget App",
                "winget_id": "Vendor.WingetApp",
                "source": "winget",
            }
        ],
    )

    appx_matches = app_tools.find_uninstall_entries({"query": "store"}, _settings_context())["matches"]
    winget_matches = app_tools.find_uninstall_entries({"query": "winget"}, _settings_context())["matches"]

    assert appx_matches[0]["uninstall_method"] == "appx"
    assert winget_matches[0]["uninstall_method"] == "winget"


def test_uninstall_app_blocks_scanned_shell_host(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: [])
    monkeypatch.setattr(app_tools, "_scan_winget_packages", lambda: [])
    monkeypatch.setattr(
        app_tools,
        "_scan_registry_apps",
        lambda: [
            {
                "id": "bad product",
                "name": "Bad Product",
                "publisher": "Vendor",
                "uninstall_string": "cmd.exe /c calc.exe",
                "source": "registry",
            }
        ],
    )

    result = app_tools.uninstall_app({"query": "Bad Product", "dry_run": False}, _settings_context())

    assert result["ok"] is False
    assert "shell/script host" in result["error"]


def test_app_allowlist_supports_wildcards_and_categories(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_APP_ALLOWLIST="visual*;category:browser")
    monkeypatch.setattr(
        app_tools,
        "_scan_shortcuts",
        lambda: [
            {"id": "visual studio code", "name": "Visual Studio Code", "path": "Code.exe", "source": "start_menu"},
            {"id": "google chrome", "name": "Google Chrome", "path": "chrome.exe", "source": "start_menu"},
            {"id": "paint", "name": "Paint", "path": "mspaint.exe", "source": "start_menu"},
        ],
    )
    monkeypatch.setattr(app_tools, "_scan_registry_apps", lambda: [])
    context = _settings_context()

    apps = app_tools.list_installed({}, context)["apps"]
    launch = app_tools.launch_installed({"app": "google chrome", "dry_run": True}, context)

    code = next(app for app in apps if app["id"] == "visual studio code")
    chrome = next(app for app in apps if app["id"] == "google chrome")
    paint = next(app for app in apps if app["id"] == "paint")
    assert code["allowlisted"] is True
    assert code["allowlist_match"] == "visual*"
    assert chrome["allowlisted"] is True
    assert chrome["allowlist_match"] == "category:browser"
    assert paint["allowlisted"] is False
    assert launch["ok"] is True
    assert launch["allowlist_match"] == "category:browser"


def test_app_open_authorized_file_and_folder_dry_run(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sample = workspace / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_ALLOWED_DIRECTORIES=str(workspace))
    context = _settings_context()

    file_result = app_tools.open_file({"path": str(sample), "dry_run": True}, context)
    folder_result = app_tools.open_folder({"path": str(workspace), "dry_run": True}, context)

    assert file_result == {"ok": True, "dry_run": True, "path": str(sample.resolve())}
    assert folder_result == {"ok": True, "dry_run": True, "path": str(workspace.resolve())}


def test_app_open_file_api_rejects_dangerous_shell_association(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "payload.cmd"
    script.write_text("@echo unsafe", encoding="utf-8")
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_ALLOWED_DIRECTORIES=str(workspace))
    calls: list[str] = []
    monkeypatch.setattr(app_tools.os, "startfile", lambda path: calls.append(str(path)), raising=False)

    response = TestClient(create_app()).post("/api/apps/open-file", json={"path": str(script)})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "unsafe_file_open"
    assert calls == []


@pytest.mark.parametrize("extension", [".exe", ".ps1", ".lnk", ".url", ".msi"])
def test_app_open_file_api_rejects_other_dangerous_associations(monkeypatch, tmp_path, extension):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = workspace / f"payload{extension}"
    payload.write_text("unsafe", encoding="utf-8")
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_ALLOWED_DIRECTORIES=str(workspace))
    calls: list[str] = []
    monkeypatch.setattr(app_tools.os, "startfile", lambda path: calls.append(str(path)), raising=False)

    response = TestClient(create_app()).post("/api/apps/open-file", json={"path": str(payload)})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "unsafe_file_open"
    assert calls == []


def test_app_open_file_aborts_before_startfile(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sample = workspace / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_ALLOWED_DIRECTORIES=str(workspace))
    calls: list[str] = []
    abort = threading.Event()
    abort.set()
    monkeypatch.setattr(app_tools.os, "startfile", lambda path: calls.append(str(path)), raising=False)

    with pytest.raises(ToolAbortedError):
        app_tools.open_file(
            {"path": str(sample), "dry_run": False},
            {**_settings_context(), "_tool_abort_event": abort},
        )

    assert calls == []


def test_app_launch_allowlisted_aborts_before_popen(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    calls: list[object] = []
    abort = threading.Event()
    abort.set()
    monkeypatch.setattr(app_tools.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(ToolAbortedError):
        app_tools.launch_allowlisted({"app": "notepad", "dry_run": False}, {"_tool_abort_event": abort})

    assert calls == []


def test_system_diagnostics_startup_and_settings_dry_run(monkeypatch):
    monkeypatch.setattr(system_tools, "get_info", lambda args, context: {"memory_total": 1024, "memory_available": 768})
    monkeypatch.setattr(system_tools, "get_disks", lambda args, context: {"disks": []})
    monkeypatch.setattr(system_tools, "get_network", lambda args, context: {"network": {}})
    monkeypatch.setattr(system_tools, "get_battery", lambda args, context: {"battery": None})
    monkeypatch.setattr(system_tools, "get_processes", lambda args, context: {"processes": []})
    diagnostics = system_tools.diagnostics({}, {})
    startup = system_tools.get_startup_items({}, {})
    settings = system_tools.open_settings_uri({"uri": "ms-settings:display", "dry_run": True}, {})

    assert {"info", "disks", "network", "battery", "top_processes", "suggestions"}.issubset(diagnostics)
    assert diagnostics["local_ai"]["probe_mode"] == "summary_only"
    assert isinstance(startup["startup_items"], list)
    assert settings == {"ok": True, "dry_run": True, "uri": "ms-settings:display"}


def test_system_diagnostics_redacts_best_effort_errors(monkeypatch):
    sensitive_error = "failed at C:\\Users\\Suli\\Desktop\\secrets\\.env with sk-system-secret-1234567890"

    fake_psutil = types.SimpleNamespace()
    fake_psutil.cpu_count = lambda: (_ for _ in ()).throw(RuntimeError(sensitive_error))
    fake_psutil.virtual_memory = lambda: types.SimpleNamespace(total=0, available=0)
    fake_psutil.disk_partitions = lambda all=False: [  # noqa: A002 - mirrors psutil signature.
        types.SimpleNamespace(mountpoint="C:\\", device="C:", fstype="NTFS", opts="")
    ]
    fake_psutil.disk_usage = lambda _mountpoint: (_ for _ in ()).throw(RuntimeError(sensitive_error))
    fake_psutil.net_if_addrs = lambda: (_ for _ in ()).throw(RuntimeError(sensitive_error))
    fake_psutil.sensors_battery = lambda: (_ for _ in ()).throw(RuntimeError(sensitive_error))
    fake_psutil.process_iter = lambda _attrs: (_ for _ in ()).throw(RuntimeError(sensitive_error))
    fake_psutil.NoSuchProcess = RuntimeError
    fake_psutil.AccessDenied = PermissionError
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    payload = {
        "info": system_tools.get_info({}, {}),
        "disks": system_tools.get_disks({}, {}),
        "network": system_tools.get_network({}, {}),
        "battery": system_tools.get_battery({}, {}),
        "processes": system_tools.get_processes({}, {}),
    }
    serialized = str(payload)

    assert "C:\\Users\\Suli" not in serialized
    assert "sk-system-secret-1234567890" not in serialized
    assert "[REDACTED_LOCAL_PATH]" in serialized
    assert "[REDACTED_API_KEY]" in serialized


def test_system_open_settings_aborts_before_startfile(monkeypatch):
    abort = threading.Event()
    abort.set()
    calls: list[str] = []
    monkeypatch.setattr(system_tools.platform, "system", lambda: "Windows")
    monkeypatch.setattr(system_tools.os, "startfile", lambda uri: calls.append(str(uri)), raising=False)

    with pytest.raises(ToolAbortedError):
        system_tools.open_settings_uri({"uri": "ms-settings:display", "dry_run": False}, {"_tool_abort_event": abort})

    assert calls == []


def test_browser_network_gate_blocks_when_disabled(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_ALLOW_BROWSER_NETWORK="false")

    result = browser_tools.read_page({"url": "http://127.0.0.1:9"}, _settings_context())

    assert result["ok"] is False
    assert "disabled" in result["error"].lower()


def test_browser_read_page_and_extract_links_with_local_http(monkeypatch, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        "<!doctype html><title>Lengrvis Test</title><main>Hello office agent</main>"
        '<a href="/docs">Docs</a><a href="https://example.com/ext">External</a>',
        encoding="utf-8",
    )
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_ALLOW_BROWSER_NETWORK="true")
    # Loopback test server requires the explicit private-host opt-in (SSRF guard).
    monkeypatch.setenv("LENGRVIS_BROWSER_ALLOW_PRIVATE_HOSTS", "1")

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args):  # noqa: A002
            return

    with socketserver.TCPServer(
        ("127.0.0.1", 0), lambda *args, **kwargs: QuietHandler(*args, directory=str(site), **kwargs)
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/index.html"

        page = browser_tools.read_page({"url": url, "max_chars": 500}, _settings_context())
        links = browser_tools.extract_links({"url": url, "max_chars": 500}, _settings_context())

        server.shutdown()
        thread.join(timeout=2)

    assert page["ok"] is True
    assert page["title"] == "Lengrvis Test"
    assert "Hello office agent" in page["text"]
    assert any(link["url"].endswith("/docs") for link in links["links"])


def test_search_query_delegates_to_browser_gate(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_ALLOW_BROWSER_NETWORK="false")

    result = search_tools.query({"query": "lengrvis"}, _settings_context())

    assert result["ok"] is False
    assert "disabled" in result["error"].lower()


def test_public_api_routes_expose_windows_core(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, LENGRVIS_ALLOW_BROWSER_NETWORK="false")
    monkeypatch.setattr(
        system_service,
        "diagnostics",
        lambda: {
            "info": {"memory_total": 1024, "memory_available": 768},
            "disks": [],
            "network": {},
            "battery": None,
            "top_processes": [],
            "local_ai": {"scope": "local_only", "probe_mode": "summary_only"},
            "suggestions": ["No critical system issue detected from read-only diagnostics."],
        },
    )
    monkeypatch.setattr(system_service, "processes", lambda limit=25: {"processes": [], "count": 0})
    monkeypatch.setattr(system_service, "startup_items", lambda: {"startup_items": [], "count": 0})
    monkeypatch.setattr(
        ui_automation_tools, "active_window", lambda args, context: {"ok": True, "title": "Test Window"}
    )
    monkeypatch.setattr(ui_automation_tools, "observe", lambda args, context: {"ok": True, "elements": []})
    client = TestClient(create_app())

    assert client.get("/api/apps").status_code == 200
    assert client.get("/api/system/diagnostics").status_code == 200
    assert client.get("/api/system/processes").status_code == 200
    assert client.get("/api/system/startup-items").status_code == 200
    assert client.get("/api/browser/read", params={"url": "https://example.com"}).json()["ok"] is False
    assert client.get("/api/browser/links", params={"url": "https://example.com"}).json()["ok"] is False
    assert client.get("/api/ui-automation/active-window").status_code == 200
    assert client.post("/api/ui-automation/observe", json={"max_depth": 0}).status_code == 200


def test_ui_automation_api_dry_run_creates_bound_approval(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    client = TestClient(create_app())

    response = client.post(
        "/api/ui-automation/action",
        json={"action": "click", "name": "OK", "control_type": "Button"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "requires_approval"
    assert payload["approval_id"]
    approval = db.fetch_one("approvals", payload["approval_id"])
    assert approval is not None
    assert approval["tool_name"] == "ui_automation.click"
    assert approval["status"] == "pending"
    assert approval["args_binding_hmac"].startswith("args:")
    assert approval["preview_hmac"].startswith("preview:")


def test_ui_automation_api_revalidates_approval_after_claim(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    import app.api.routes_ui_automation as routes_ui_automation

    calls: list[dict] = []

    def fake_click(args, context):  # noqa: ANN001, ANN202
        calls.append({"args": dict(args), "context": dict(context)})
        return {"ok": True}

    monkeypatch.setattr(routes_ui_automation.ui_automation_tools, "click", fake_click)
    payload = {
        "action": "click",
        "name": "OK",
        "control_type": "Button",
        "dry_run": False,
        "approved": True,
    }
    settings = _settings_context()["settings"]
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "name": "OK"}]}
    approval = Approval(
        task_id="direct_ui_automation_api",
        step_id=None,
        message="Approve GUI click",
        status=ApprovalStatus.APPROVED,
        tool_name="ui_automation.click",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY.value,
        preview_hmac=preview_hmac(preview),
        settings_fingerprint=settings_fingerprint(settings, allowed_directories=settings.allowed_directories),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version="1",
        diff_preview=preview,
    )
    payload["approval_id"] = approval.id
    approval.args_binding_hmac = args_binding_hmac(
        "ui_automation.click",
        {key: value for key, value in payload.items() if key not in {"approved", "approval_id", "dry_run"}},
        task_id=approval.task_id,
        step_id=approval.step_id,
    )
    db.upsert_model("approvals", approval, status=approval.status)
    original_claim = db.claim_approval_for_execution

    def claim_and_tamper(approval_id: str, consumed_at: str):
        claimed = original_claim(approval_id, consumed_at)
        if claimed:
            claimed["tool_name"] = "ui_automation.hotkey"
        return claimed

    monkeypatch.setattr(routes_ui_automation.db, "claim_approval_for_execution", claim_and_tamper)
    client = TestClient(create_app())

    response = client.post("/api/ui-automation/action", json=payload)

    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "tool name" in result["error"].lower()
    assert calls == []
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed.consumed_at


def test_ui_automation_api_blocks_unknown_and_sensitive_actions(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    client = TestClient(create_app())

    unknown = client.post("/api/ui-automation/action", json={"action": "launch_missiles"}).json()
    sensitive = client.post(
        "/api/ui-automation/action",
        json={"action": "type_text", "name": "password", "text": "hello"},
    ).json()

    assert unknown["ok"] is False
    assert unknown["status"] == "denied"
    assert sensitive["ok"] is False
    assert sensitive["status"] == "denied"
    assert sensitive["review"]["risk_level"] == RiskLevel.R4_FORBIDDEN_OR_HANDOFF.value


def test_policy_rejects_chinese_sensitive_goal():
    review = PolicyEngine().review_goal_text("task_cn", "读取浏览器 cookie token 和密码")

    assert review.verdict == "deny"
    assert review.risk_level == "R4_FORBIDDEN_OR_HANDOFF"
