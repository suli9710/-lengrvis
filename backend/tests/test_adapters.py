from __future__ import annotations

from typing import Any

from app.adapters.base import AdapterConfig
from app.adapters.tools import create_adapter_tool_definitions
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel
from app.tools.registry import ToolRegistry, register_all_tools


class FakeEmailClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_message(self, message: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(message)
        return {"ok": True, "message_id": f"email-{len(self.sent)}"}


class FakeCalendarClient:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def create_event(self, event: dict[str, Any]) -> dict[str, Any]:
        self.events.append(event)
        return {"ok": True, "event_id": f"event-{len(self.events)}", "html_link": "https://calendar.test/event-1"}


def test_send_email_tool_executes_end_to_end_with_fake_client():
    client = FakeEmailClient()
    registry = ToolRegistry()
    for definition in create_adapter_tool_definitions(
        email_client=client,
        configs={"email": AdapterConfig(service_name="email", dry_run=False, test_mode=True)},
    ):
        registry.register(definition)

    result = registry.get("external.email.send").execute(
        {
            "to": ["ada@example.test"],
            "subject": "Phase 5.3",
            "body": "Adapter test path",
            "dry_run": False,
        },
        {},
    )

    assert result["ok"] is True
    assert result["message_id"] == "email-1"
    assert result["message"]["subject"] == "Phase 5.3"
    assert client.sent == [result["message"]]


def test_create_calendar_event_tool_executes_end_to_end_with_fake_client():
    client = FakeCalendarClient()
    registry = ToolRegistry()
    for definition in create_adapter_tool_definitions(
        calendar_client=client,
        configs={"calendar": AdapterConfig(service_name="calendar", dry_run=False, test_mode=True)},
    ):
        registry.register(definition)

    result = registry.get("external.calendar.create_event").execute(
        {
            "title": "Adapter Review",
            "start": "2026-05-26T09:00:00+08:00",
            "end": "2026-05-26T09:30:00+08:00",
            "timezone": "Asia/Shanghai",
            "attendees": ["grace@example.test"],
            "dry_run": False,
        },
        {},
    )

    assert result["ok"] is True
    assert result["event_id"] == "event-1"
    assert result["event"]["timezone"] == "Asia/Shanghai"
    assert client.events == [result["event"]]


def test_adapter_tools_are_registered_with_backend_tool_registry():
    registry = register_all_tools(load_skills=False)

    assert registry.get("external.email.send").supports_dry_run is True
    assert registry.get("external.calendar.create_event").supports_dry_run is True
    assert registry.get("external.webhook.post").supports_dry_run is True


def test_external_adapter_tools_are_classified_as_modifying():
    policy = PolicyEngine()

    assert policy.classify_tool_name("external.email.send") == RiskLevel.R2_REVERSIBLE_MODIFY
    assert policy.classify_tool_name("external.calendar.create_event") == RiskLevel.R2_REVERSIBLE_MODIFY
    assert policy.classify_tool_name("external.webhook.post") == RiskLevel.R2_REVERSIBLE_MODIFY


def test_webhook_dry_run_redacts_sensitive_headers():
    tool = next(item for item in create_adapter_tool_definitions() if item.name == "external.webhook.post")

    result = tool.execute(
        {
            "url": "https://hooks.example.test/lengrvis",
            "payload": {"text": "hello"},
            "headers": {"Authorization": "Bearer secret-token", "X-Trace": "trace-1"},
            "dry_run": True,
        },
        {},
    )

    assert result["ok"] is True
    assert result["request"]["headers"]["Authorization"] == "***"
    assert result["request"]["headers"]["X-Trace"] == "trace-1"
    assert "secret-token" not in str(result)


def test_webhook_dry_run_redacts_sensitive_payload_fields_recursively():
    tool = next(item for item in create_adapter_tool_definitions() if item.name == "external.webhook.post")

    result = tool.execute(
        {
            "url": "https://hooks.example.test/lengrvis",
            "payload": {
                "message": "hello",
                "api_key": "payload-secret",
                "nested": [{"token": "nested-secret"}, {"safe": "visible"}],
            },
            "dry_run": True,
        },
        {},
    )

    assert result["ok"] is True
    assert result["request"]["payload"]["api_key"] == "***"
    assert result["request"]["payload"]["nested"][0]["token"] == "***"  # noqa: S105 - redaction marker.
    assert result["request"]["payload"]["nested"][1]["safe"] == "visible"
    assert "payload-secret" not in str(result)
    assert "nested-secret" not in str(result)


def test_webhook_execute_pins_outbound_url(monkeypatch):
    from app.adapters.base import AdapterConfig
    from app.adapters.webhook import WebhookAdapter

    class RecordingClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

        def post(
            self,
            url: str,
            payload: dict,
            headers: dict[str, str],
            timeout_seconds: float,
            *,
            extensions: dict[str, str],
        ) -> dict:
            self.calls.append((url, headers, extensions))
            return {"ok": True}

    client = RecordingClient()
    adapter = WebhookAdapter(
        config=AdapterConfig(service_name="webhook", dry_run=False, test_mode=False),
        client=client,
    )
    adapter.connect()
    monkeypatch.setattr(
        "app.adapters.webhook.pin_outbound_http_url",
        lambda url, allow_private=False: type(
            "Pinned",
            (),
            {
                "url": "https://203.0.113.10/hook",
                "headers": {"Host": "hooks.example.test"},
                "extensions": {"sni_hostname": "hooks.example.test"},
            },
        )(),
    )

    result = adapter.execute(
        "post_webhook",
        {
            "url": "https://hooks.example.test/hook",
            "payload": {"text": "hello"},
            "headers": {"Host": "evil.example.test", "X-Trace": "1"},
        },
    )

    assert result["ok"] is True
    assert client.calls == [
        (
            "https://203.0.113.10/hook",
            {"Host": "hooks.example.test", "X-Trace": "1"},
            {"sni_hostname": "hooks.example.test"},
        )
    ]
