from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app.config import AppSettings
from app.core import butler_store as store
from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus
from app.core.schemas import Task
from app.orchestration.agent_bus import AgentBus
from app.orchestration.runtime_context import TaskRuntimeContext
from app.policy.execution_marker import mark_execution_approved
from app.services.browser_activity_runtime import BrowserActivityRuntime, BrowserSession
from app.services.browser_execution_grants import issue_browser_command


def test_persistent_session_is_reused_only_for_the_same_task_account_and_origin(monkeypatch):
    runtime = BrowserActivityRuntime()
    monkeypatch.setattr("app.services.browser_activity_runtime._validate_url", lambda url: url)
    context = {"settings": AppSettings(allow_browser_network=True), "task_id": "task-a", "butler_matter_id": "matter-a"}
    first = runtime.ensure_session({"url": "https://example.com/form", "account_id": "account-a"}, context)
    repeated = runtime.ensure_session({"url": "https://example.com/next", "account_id": "account-a"}, context)
    other = runtime.ensure_session({"url": "https://example.com/form", "account_id": "account-b"}, context)
    assert first.id == repeated.id
    assert other.id != first.id
    with pytest.raises(ValueError):
        runtime.ensure_session({"session_id": first.id}, {**context, "task_id": "task-b"})


def test_source_read_does_not_reuse_another_page_on_the_same_origin(monkeypatch):
    runtime = BrowserActivityRuntime()
    monkeypatch.setattr("app.services.browser_activity_runtime._validate_url", lambda url: url)
    context = {"settings": AppSettings(allow_browser_network=True), "task_id": "task-a", "butler_matter_id": "matter-a"}
    first = runtime.ensure_session({"url": "https://example.com/first"}, context)
    target = "https://example.com/second"
    exact = {**context, "browser_exact_read_url": target}
    second = runtime.ensure_session({"url": target}, exact)
    assert first.id != second.id
    assert runtime.ensure_session({"url": target}, exact).id == second.id
    with pytest.raises(ValueError, match="requested page"):
        runtime.ensure_session({"session_id": first.id, "url": target}, exact)


def test_browser_write_authorization_binds_real_approval_and_snapshot(monkeypatch):
    monkeypatch.setattr("app.services.browser_execution_grants.desktop_api_token", lambda: "desktop-test-key")
    scope = store.save(AutonomyScope(label="site", origins=["https://example.com"], account_id="account"))
    task = Task(
        user_goal="Fill the allowed form",
        metadata={"butler_matter_id": "matter-a", "butler_execution_ticket": "dispatch-a"},
    )
    store.save(
        ButlerMatter(
            id="matter-a",
            goal=task.user_goal,
            status=MatterStatus.RUNNING,
            active_task_id=task.id,
            execution_ticket="dispatch-a",
            scope_versions={scope.id: 1},
        )
    )
    runtime = TaskRuntimeContext.from_task(task, AppSettings(), AgentBus())
    context = {"runtime": runtime, "approval_id": "approval-a"}
    session = BrowserSession(
        id="session-a",
        task_id=task.id,
        account_id="account",
        current_url="https://example.com/form",
        allowed_origins=["https://example.com"],
    )
    action = {"kind": "fill", "url": session.current_url, "fields": {"#notes": "ordinary text"}}
    with pytest.raises(ValueError, match="approval"):
        issue_browser_command(session, action, context)
    mark_execution_approved(context)
    with pytest.raises(ValueError, match="snapshot"):
        issue_browser_command(session, action, context)
    context["_expected_resource_state"] = [
        {"kind": "browser_control", "session_id": session.id, "fingerprint": "a" * 64}
    ]
    grant = issue_browser_command(session, action, context)
    claim = json.loads(base64.b64decode(grant["payload"]))
    assert claim["action"] == action and claim["task_id"] == task.id
    assert claim["approval_id"] == "approval-a" and claim["expected_fingerprint"] == "a" * 64
    expected = hmac.new(
        b"desktop-test-key", ("browser-execution/v1:" + grant["payload"]).encode(), hashlib.sha256
    ).hexdigest()
    assert hmac.compare_digest(grant["signature"], expected)
    from app.services.browser_activity_runtime import _requested_session_identity

    assert _requested_session_identity(
        {"url": session.current_url}, {}, {**context, "task_id": task.id, "butler_matter_id": "matter-a"}
    ) == (task.id, "account")
