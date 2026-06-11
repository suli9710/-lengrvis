from __future__ import annotations

import http.server
import socketserver
import threading
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


def test_uninstall_app_executes_scanned_entry_without_shell(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
    monkeypatch.setattr(
        app_tools,
        "_scan_registry_apps",
        lambda: [
            {
                "id": "sample product",
                "name": "Sample Product",
                "publisher": "Vendor",
                "uninstall_string": "MsiExec.exe /I {ABC-123}",
                "source": "registry",
            }
        ],
    )
    launched: list[dict[str, object]] = []

    def fake_popen(command, *, shell):  # noqa: ANN001
        launched.append({"command": command, "shell": shell})

    monkeypatch.setattr(app_tools.subprocess, "Popen", fake_popen)

    result = app_tools.uninstall_app({"query": "Sample Product", "dry_run": False}, _settings_context())

    assert result["ok"] is True
    assert launched == [{"command": ["MsiExec.exe", "/X", "{ABC-123}"], "shell": False}]


def test_uninstall_app_blocks_scanned_shell_host(monkeypatch, tmp_path):
    _init_test_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(app_tools, "_scan_shortcuts", lambda: [])
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

    with socketserver.TCPServer(("127.0.0.1", 0), lambda *args, **kwargs: QuietHandler(*args, directory=str(site), **kwargs)) as server:
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
    monkeypatch.setattr(ui_automation_tools, "active_window", lambda args, context: {"ok": True, "title": "Test Window"})
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
