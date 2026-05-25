from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.main import create_app
from app.policy.policy_engine import PolicyEngine
from app.tools import app_tools, browser_tools, search_tools, system_tools


def _init_test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    db.init_db()


def _settings_context():
    from app.llm.registry import get_effective_settings

    settings = get_effective_settings()
    return {"settings": settings, "allowed_directories": settings.allowed_directories}


def test_app_list_and_allowlisted_launch_dry_run(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, MARVIS_APP_ALLOWLIST="notepad;calc")
    context = _settings_context()

    apps = app_tools.list_installed({}, context)
    launch = app_tools.launch_installed({"app": "notepad", "dry_run": True}, context)

    assert any(app["id"] == "notepad" for app in apps["apps"])
    assert launch == {"ok": True, "dry_run": True, "command": "notepad.exe"}


def test_app_launch_unknown_application_is_blocked(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, MARVIS_APP_ALLOWLIST="notepad")
    result = app_tools.launch_installed({"app": "unknown-app", "dry_run": True}, _settings_context())

    assert result["ok"] is False
    assert "allowlisted" in result["error"]


def test_app_allowlist_supports_wildcards_and_categories(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, MARVIS_APP_ALLOWLIST="visual*;category:browser")
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
    _init_test_settings(monkeypatch, tmp_path, MARVIS_ALLOWED_DIRECTORIES=str(workspace))
    context = _settings_context()

    file_result = app_tools.open_file({"path": str(sample), "dry_run": True}, context)
    folder_result = app_tools.open_folder({"path": str(workspace), "dry_run": True}, context)

    assert file_result == {"ok": True, "dry_run": True, "path": str(sample.resolve())}
    assert folder_result == {"ok": True, "dry_run": True, "path": str(workspace.resolve())}


def test_system_diagnostics_startup_and_settings_dry_run():
    diagnostics = system_tools.diagnostics({}, {})
    startup = system_tools.get_startup_items({}, {})
    settings = system_tools.open_settings_uri({"uri": "ms-settings:display", "dry_run": True}, {})

    assert {"info", "disks", "network", "battery", "top_processes", "suggestions"}.issubset(diagnostics)
    assert isinstance(startup["startup_items"], list)
    assert settings == {"ok": True, "dry_run": True, "uri": "ms-settings:display"}


def test_browser_network_gate_blocks_when_disabled(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, MARVIS_ALLOW_BROWSER_NETWORK="false")

    result = browser_tools.read_page({"url": "http://127.0.0.1:9"}, _settings_context())

    assert result["ok"] is False
    assert "disabled" in result["error"].lower()


def test_browser_read_page_and_extract_links_with_local_http(monkeypatch, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        "<!doctype html><title>Marvis Test</title><main>Hello office agent</main>"
        '<a href="/docs">Docs</a><a href="https://example.com/ext">External</a>',
        encoding="utf-8",
    )
    _init_test_settings(monkeypatch, tmp_path, MARVIS_ALLOW_BROWSER_NETWORK="true")

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args):  # noqa: A002
            return

    with socketserver.TCPServer(("127.0.0.1", 0), lambda *args, **kwargs: QuietHandler(*args, directory=str(site), **kwargs)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/index.html"

        page = browser_tools.read_page({"url": url, "max_chars": 500}, _settings_context())
        links = browser_tools.extract_links({"url": url, "max_chars": 500}, _settings_context())

        server.shutdown()
        thread.join(timeout=2)

    assert page["ok"] is True
    assert page["title"] == "Marvis Test"
    assert "Hello office agent" in page["text"]
    assert any(link["url"].endswith("/docs") for link in links["links"])


def test_search_query_delegates_to_browser_gate(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, MARVIS_ALLOW_BROWSER_NETWORK="false")

    result = search_tools.query({"query": "marvis"}, _settings_context())

    assert result["ok"] is False
    assert "disabled" in result["error"].lower()


def test_public_api_routes_expose_windows_core(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path, MARVIS_ALLOW_BROWSER_NETWORK="false")
    client = TestClient(create_app())

    assert client.get("/api/apps").status_code == 200
    assert client.get("/api/system/diagnostics").status_code == 200
    assert client.get("/api/system/processes").status_code == 200
    assert client.get("/api/system/startup-items").status_code == 200
    assert client.get("/api/browser/read", params={"url": "https://example.com"}).json()["ok"] is False
    assert client.get("/api/browser/links", params={"url": "https://example.com"}).json()["ok"] is False


def test_policy_rejects_chinese_sensitive_goal():
    review = PolicyEngine().review_goal_text("task_cn", "读取浏览器 cookie token 和密码")

    assert review.verdict == "deny"
    assert review.risk_level == "R4_FORBIDDEN_OR_HANDOFF"
