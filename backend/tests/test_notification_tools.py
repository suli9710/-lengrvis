from __future__ import annotations

from app.tools import notification_tools
from app.tools.registry import ToolRegistry


def test_notification_tool_dry_run_does_not_queue(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(notification_tools.notification_service, "notify", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = notification_tools.send(
        {"title": "完成", "body": "任务已处理", "dry_run": True},
        {"task_id": "task-1"},
    )

    assert result["dry_run"] is True
    assert result["queued"] is False
    assert calls == []


def test_notification_tool_queues_through_notification_service(monkeypatch) -> None:
    calls: list[tuple] = []

    def notify(title: str, body: str, **kwargs):
        calls.append((title, body, kwargs))
        return {"queued": True, "title": title, "body": body, "task_id": kwargs["task_id"], "severity": "info"}

    monkeypatch.setattr(notification_tools.notification_service, "notify", notify)
    result = notification_tools.send(
        {"title": "完成", "body": "任务已处理"},
        {"task_id": "task-1"},
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert calls == [("完成", "任务已处理", {"task_id": "task-1", "severity": "info"})]


def test_notification_tool_contract_is_governed_write() -> None:
    registry = ToolRegistry()
    notification_tools.register(registry)
    tool = registry.get("notification.send")

    assert tool.supports_dry_run is True
    assert tool.is_read_only() is False
    assert tool.is_concurrency_safe() is False
    assert tool.effects == ["notify", "write"]
    assert tool.sensitive_arg_keys == ["body"]
