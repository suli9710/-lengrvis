from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import AppSettings
from app.core import db
from app.services.browser_activity_runtime import BrowserActivityRuntime, _read_limited_http_response
from app.tools import browser_tools


class FakeBrowserAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def perform(self, session, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN001
        self.calls.append({"session_id": session.id, "action": dict(action)})
        kind = action["kind"]
        url = action.get("url") or session.current_url or "https://example.test/home?token=secret-token"
        if kind == "screenshot":
            return {"ok": True, "url": url, "title": "Example", "screenshot_url": "file:///tmp/browser.png"}
        if kind in {"observe", "navigate", "open"}:
            return {
                "ok": True,
                "url": url,
                "title": "Example",
                "text": "Visible page text containing Alice and secret-token.",
                "links": [{"title": "Docs", "url": "https://example.test/docs"}],
            }
        return {"ok": True, "url": url, "title": "Example", "changed_paths": [], "rollback_info": {}}


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield
    browser_tools.reset_browser_activity_runtime()


def _context(*, mode: str = "efficiency", allow_browser_network: bool = True) -> dict[str, Any]:
    settings = AppSettings(
        provider_name="mock",
        mode=mode,
        allow_browser_network=allow_browser_network,
        allow_cloud_context=True,
    )
    return {"settings": settings, "allowed_directories": []}


def test_runtime_starts_session_observes_and_records_redacted_events() -> None:
    runtime = BrowserActivityRuntime(adapter=FakeBrowserAdapter())

    started = runtime.session_start(
        {
            "task_id": "task-1",
            "step_id": "step-1",
            "url": "https://example.test/page?token=secret-token&name=Alice",
        },
        _context(),
    )
    observed = runtime.observe({"session_id": started["session"]["id"], "step_id": "step-2"}, _context())
    events = runtime.events({"session_id": started["session"]["id"]})["events"]

    assert started["ok"] is True
    assert observed["ok"] is True
    assert [event["type"] for event in events] == ["session.start", "observe"]
    assert events[-1]["task_id"] == "task-1"
    assert events[-1]["result"]["link_count"] == 1
    assert "secret-token" not in str(events)
    assert "Alice" not in str(events)

    audit_events = db.fetch_many("audit_events", "task_id = ?", ("task-1",), limit=10)
    assert any(event["event_type"] == "browser_activity.observe" for event in audit_events)
    assert "secret-token" not in str(audit_events)


def test_write_action_dry_run_preview_is_redacted_and_does_not_execute() -> None:
    adapter = FakeBrowserAdapter()
    runtime = BrowserActivityRuntime(adapter=adapter)
    started = runtime.session_start({"task_id": "task-2"}, _context())

    preview = runtime.act(
        {
            "session_id": started["session"]["id"],
            "task_id": "task-2",
            "action": {
                "kind": "click",
                "url": "https://example.test/account?token=secret-token",
                "selector": "#go-button",
                "text": "Alice",
            },
            "dry_run": True,
        },
        _context(),
    )

    assert preview["ok"] is True
    assert preview["dry_run"] is True
    assert adapter.calls == []
    assert "secret-token" not in str(preview)
    assert "#go-button" not in str(preview)
    assert "Alice" not in str(preview)


def test_live_write_action_requires_approval_then_records_sanitized_event() -> None:
    adapter = FakeBrowserAdapter()
    runtime = BrowserActivityRuntime(adapter=adapter)
    started = runtime.session_start({"task_id": "task-3"}, _context())
    args = {
        "session_id": started["session"]["id"],
        "task_id": "task-3",
        "action": {
            "kind": "fill",
            "url": "https://example.test/profile?token=secret-token",
            "fields": {"#email": "alice@example.test"},
        },
        "dry_run": False,
    }

    blocked = runtime.act(args, _context())
    allowed = runtime.act({**args, "approved": True, "approval_id": "approval-1"}, _context())
    events = runtime.events({"session_id": started["session"]["id"]})["events"]
    replay = runtime.replay_export({"session_id": started["session"]["id"]}, _context())

    assert blocked["ok"] is False
    assert "approval_id" in blocked["error"]
    assert allowed["ok"] is True
    assert adapter.calls[-1]["action"]["kind"] == "fill"
    assert any(event["type"] == "act.fill" and event["ok"] is True for event in events)
    assert "secret-token" not in str(events)
    assert "alice@example.test" not in str(replay)


def test_live_fill_with_sensitive_value_is_blocked_before_adapter() -> None:
    adapter = FakeBrowserAdapter()
    runtime = BrowserActivityRuntime(adapter=adapter)
    started = runtime.session_start({"task_id": "task-sensitive-fill"}, _context())

    result = runtime.act(
        {
            "session_id": started["session"]["id"],
            "task_id": "task-sensitive-fill",
            "action": {
                "kind": "fill",
                "url": "https://example.test/profile",
                "fields": {"#notes": "4111111111111111"},
            },
            "dry_run": False,
            "approved": True,
            "approval_id": "approval-card",
        },
        _context(),
    )

    assert result["ok"] is False
    assert "sensitive" in result["error"].lower()
    assert adapter.calls == []


def test_legacy_read_page_uses_session_compatible_runtime_flow() -> None:
    adapter = FakeBrowserAdapter()
    browser_tools.reset_browser_activity_runtime(adapter=adapter)

    page = browser_tools.read_page(
        {"url": "https://example.test/read?token=secret-token", "task_id": "task-4"},
        _context(),
    )
    events = browser_tools.get_browser_activity_runtime().events({})["events"]

    assert page["ok"] is True
    assert page["title"] == "Example"
    assert adapter.calls[-1]["action"]["kind"] == "observe"
    assert any(event["type"] == "observe" and event["task_id"] == "task-4" for event in events)
    assert "secret-token" not in str(events)


def test_httpx_observe_reads_response_with_hard_byte_limit() -> None:
    body = b"<html><title>Example</title><main>" + (b"a" * 512) + b"</main></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": str(len(body))}, content=body, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        html, final_url, truncated = _read_limited_http_response(client, "https://example.test/page", 64)

    assert final_url == "https://example.test/page"
    assert truncated is True
    assert len(html.encode("utf-8")) <= 64
    assert "a" * 128 not in html


def test_open_url_defaults_to_isolated_session_without_system_browser(monkeypatch) -> None:
    adapter = FakeBrowserAdapter()
    browser_tools.reset_browser_activity_runtime(adapter=adapter)
    opened: list[str] = []
    monkeypatch.setattr(browser_tools.webbrowser, "open", lambda url, new=0: opened.append(url))

    result = browser_tools.open_url({"url": "https://example.test/page?token=secret-token", "task_id": "task-5"}, _context())
    events = browser_tools.get_browser_activity_runtime().events({"task_id": "task-5"})["events"]

    assert result["ok"] is True
    assert result["isolated_session"] is True
    assert opened == []
    assert events[0]["type"] == "session.start"
    assert "secret-token" not in str(result)
    assert "secret-token" not in str(events)


# --- SSRF guard regression (code review 3-H3 / 3-L4) -----------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/system/diagnostics",
        "http://localhost/admin",
        "http://[::1]:8000/",
        "http://10.0.0.5/",
        "http://172.16.1.1/",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://router.local/",
        "http://intranet/",
    ],
)
def test_validate_url_blocks_private_and_loopback_hosts(url: str, monkeypatch) -> None:
    from app.services.browser_activity_runtime import ALLOW_PRIVATE_HOSTS_ENV, _validate_url

    monkeypatch.delenv(ALLOW_PRIVATE_HOSTS_ENV, raising=False)
    with pytest.raises(ValueError):
        _validate_url(url)


def test_validate_url_allows_private_hosts_with_explicit_opt_in(monkeypatch) -> None:
    from app.services.browser_activity_runtime import ALLOW_PRIVATE_HOSTS_ENV, _validate_url

    monkeypatch.setenv(ALLOW_PRIVATE_HOSTS_ENV, "1")
    assert _validate_url("http://192.168.1.1/router") == "http://192.168.1.1/router"


def test_validate_url_still_allows_public_and_unresolvable_hosts(monkeypatch) -> None:
    from app.services.browser_activity_runtime import ALLOW_PRIVATE_HOSTS_ENV, _validate_url

    monkeypatch.delenv(ALLOW_PRIVATE_HOSTS_ENV, raising=False)
    # Unresolvable test domains pass validation and fail later at connect time.
    assert _validate_url("https://example.test/page") == "https://example.test/page"


def test_browser_tools_validate_url_shares_ssrf_guard(monkeypatch) -> None:
    from app.services.browser_activity_runtime import ALLOW_PRIVATE_HOSTS_ENV

    monkeypatch.delenv(ALLOW_PRIVATE_HOSTS_ENV, raising=False)
    with pytest.raises(ValueError):
        browser_tools._validate_url("http://127.0.0.1:8000/api/runs")


def test_validate_final_url_blocks_post_redirect_loopback_targets(monkeypatch) -> None:
    from app.services.browser_activity_runtime import ALLOW_PRIVATE_HOSTS_ENV, _validate_final_url

    monkeypatch.delenv(ALLOW_PRIVATE_HOSTS_ENV, raising=False)
    with pytest.raises(ValueError):
        _validate_final_url("http://127.0.0.1:8000/api/system/diagnostics")
