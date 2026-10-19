from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import db
from app.tools import app_tools


@pytest.fixture(autouse=True)
def isolated_inventory(monkeypatch):
    appx_scan = app_tools._scan_appx_packages
    winget_scan = app_tools._scan_winget_packages
    for scanner in ("_scan_shortcuts", "_scan_registry_apps", "_scan_appx_packages", "_scan_winget_packages"):
        monkeypatch.setattr(app_tools, scanner, lambda: [])
    db.init_db()
    return SimpleNamespace(appx=appx_scan, winget=winget_scan)


def context(*allowlist):
    return {"settings": SimpleNamespace(app_allowlist=list(allowlist))}


def test_launch_keeps_shortcut_when_registry_has_richer_uninstall_metadata(monkeypatch, tmp_path):
    shortcut = tmp_path / "Shared App.lnk"
    shortcut.touch()
    directory = tmp_path / "Shared App"
    directory.mkdir()
    monkeypatch.setattr(
        app_tools,
        "_scan_shortcuts",
        lambda: [{"id": "shared app", "name": "Shared App", "path": str(shortcut), "source": "start_menu"}],
    )
    monkeypatch.setattr(
        app_tools,
        "_scan_registry_apps",
        lambda: [
            {
                "id": "shared app",
                "name": "Shared App",
                "path": str(directory),
                "source": "registry",
                "publisher": "Fixture Publisher",
                "uninstall_string": str(directory / "uninstall.exe"),
            }
        ],
    )
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)

    result = app_tools.launch_installed({"app": "Shared App"}, context("shared app"))

    assert result["ok"] is True
    assert opened == [str(shortcut)]
    installed = next(app for app in app_tools.installed_apps(context("shared app")) if app["id"] == "shared app")
    assert installed["uninstall_string"] == str(directory / "uninstall.exe")


@pytest.mark.parametrize("dry_run", [False, True])
def test_installation_directory_is_not_a_launch_target(monkeypatch, tmp_path, dry_run):
    monkeypatch.setattr(
        app_tools,
        "_scan_registry_apps",
        lambda: [{"id": "example", "name": "Example", "path": str(tmp_path), "source": "registry"}],
    )
    monkeypatch.setattr(os, "startfile", lambda _: pytest.fail("Must not open an installation folder"), raising=False)

    result = app_tools.launch_installed({"app": "Example", "dry_run": dry_run}, context("example"))

    assert result["ok"] is False
    assert result.get("launched") is not True


@pytest.mark.skipif(os.name != "nt", reason="Windows built-in executable resolution")
def test_builtin_launch_does_not_search_the_working_directory(monkeypatch, tmp_path):
    decoy = tmp_path / "notepad.exe"
    decoy.write_bytes(b"test-only non-executable decoy")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    launched = []
    monkeypatch.setattr(
        app_tools.subprocess,
        "Popen",
        lambda command, **kwargs: launched.append(command) or SimpleNamespace(pid=456, poll=lambda: None),
    )

    result = app_tools.launch_allowlisted({"app": "notepad"}, context())

    executable = Path(launched[0][0])
    assert executable.is_absolute()
    assert executable.is_file()
    assert executable != decoy
    assert result["process_id"] == 456


def test_launch_does_not_borrow_registry_publisher_authorization(monkeypatch, tmp_path):
    shortcut = tmp_path / "Shared App.lnk"
    shortcut.touch()
    monkeypatch.setattr(
        app_tools,
        "_scan_shortcuts",
        lambda: [{"id": "shared app", "name": "Shared App", "path": str(shortcut), "source": "start_menu"}],
    )
    monkeypatch.setattr(
        app_tools,
        "_scan_registry_apps",
        lambda: [{"id": "shared app", "name": "Shared App", "publisher": "Trusted Vendor", "source": "registry"}],
    )
    monkeypatch.setattr(
        os, "startfile", lambda _: pytest.fail("Registry publisher cannot authorize a shortcut"), raising=False
    )

    result = app_tools.launch_installed({"app": "Shared App"}, context("publisher:Trusted Vendor"))

    assert result["ok"] is False


def test_multiple_launchers_require_an_exact_identity(monkeypatch, tmp_path):
    paths = [tmp_path / "user.lnk", tmp_path / "system.lnk"]
    for path in paths:
        path.touch()
    monkeypatch.setattr(
        app_tools,
        "_scan_shortcuts",
        lambda: [{"id": "editor", "name": "Editor", "path": str(path), "source": "start_menu"} for path in paths],
    )
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)

    ambiguous = app_tools.launch_installed({"app": "Editor"}, context("editor"))
    assert ambiguous["error_code"] == "app_launch_ambiguous"
    assert opened == []
    selected = app_tools.launch_installed({"app": str(paths[1])}, context("editor"))
    assert selected["ok"] is True
    assert selected["window_verified"] is False
    assert opened == [str(paths[1])]


@pytest.mark.parametrize("allowed", ["vendor.editor", "Vendor.Editor_abc!App", "publisher:CN=Vendor"])
def test_store_launch_resolves_registered_id_in_the_correct_package(monkeypatch, isolated_inventory, allowed):
    payload = {
        "Packages": [
            {
                "Name": "Vendor.Editor",
                "PackageFullName": "Vendor.Editor_1_x64__abc",
                "PackageFamilyName": "Vendor.Editor_abc",
                "Publisher": "CN=Vendor",
                "InstallLocation": "C:\\Apps",
            }
        ],
        "StartApps": [
            {"Name": "编辑器", "AppID": "Vendor.Editor_abc!App"},
            {"Name": "Other", "AppID": "Unrelated_abc!App"},
            {"Name": "Invalid", "AppID": "Vendor.Editor_abc!App & calc"},
        ],
    }
    monkeypatch.setattr(app_tools.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app_tools, "_system_executable", lambda _: "powershell.exe")
    monkeypatch.setattr(
        app_tools.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode("utf-8")),
    )
    records = isolated_inventory.appx()
    assert len(records[0]["launchers"]) == 1
    monkeypatch.setattr(app_tools, "_scan_appx_packages", lambda: records)
    filtered = app_tools.list_installed({"query": "编辑器"}, context(allowed))["apps"]
    assert len(filtered) == 1 and filtered[0]["id"] == "vendor.editor"
    assert filtered[0]["launchers"][0]["app_user_model_id"] == "Vendor.Editor_abc!App"
    activated = []
    monkeypatch.setattr(app_tools, "_activate_packaged_app", lambda app_id: activated.append(app_id) or 789)
    monkeypatch.setattr(
        os, "startfile", lambda _: pytest.fail("Must activate the application, not its directory"), raising=False
    )

    result = app_tools.launch_installed({"app": "编辑器"}, context(allowed))

    assert result["ok"] is True
    assert result["process_id"] == 789
    assert activated == ["Vendor.Editor_abc!App"]


def test_publisher_is_not_an_application_identity(monkeypatch, tmp_path):
    shortcut = tmp_path / "editor.lnk"
    shortcut.touch()
    monkeypatch.setattr(
        app_tools,
        "_scan_shortcuts",
        lambda: [
            {"id": "editor", "name": "Editor", "path": str(shortcut), "publisher": "Vendor", "source": "start_menu"}
        ],
    )
    monkeypatch.setattr(
        os, "startfile", lambda _: pytest.fail("Must not pick the first app from a publisher"), raising=False
    )

    assert app_tools.launch_installed({"app": "Vendor"}, context("editor"))["ok"] is False


@pytest.mark.parametrize("workspace_is_cwd", [False, True])
def test_winget_inventory_ignores_executables_inside_a_workspace(
    monkeypatch, tmp_path, isolated_inventory, workspace_is_cwd
):
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "installed-tools"
    workspace.mkdir()
    trusted.mkdir()
    engine = tmp_path / "engine"
    engine.mkdir()
    for directory in (workspace, trusted):
        (directory / "winget.exe").write_bytes(b"non-executable fixture")
    monkeypatch.chdir(workspace if workspace_is_cwd else engine)
    monkeypatch.setenv("PATH", os.pathsep.join((str(workspace), str(trusted))))
    monkeypatch.setattr(
        app_tools, "get_effective_settings", lambda: SimpleNamespace(allowed_directories=[str(workspace)])
    )
    commands = []
    monkeypatch.setattr(
        app_tools.subprocess,
        "run",
        lambda command, **kwargs: (
            commands.append(command) or SimpleNamespace(returncode=0, stdout=b'{"Packages": []}')
        ),
    )

    assert isolated_inventory.winget() == []
    assert commands[0][0] == str(trusted / "winget.exe")
    monkeypatch.setenv("PATH", str(workspace))
    commands.clear()
    assert isolated_inventory.winget() == []
    assert commands == []
