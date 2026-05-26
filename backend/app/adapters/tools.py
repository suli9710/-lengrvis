from __future__ import annotations

from typing import Any

from app.adapters.base import AdapterConfig
from app.adapters.calendar import CalendarAdapter, CalendarClient
from app.adapters.email import EmailAdapter, EmailClient
from app.adapters.webhook import WebhookAdapter, WebhookClient
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def create_adapter_tool_definitions(
    *,
    email_adapter: EmailAdapter | None = None,
    calendar_adapter: CalendarAdapter | None = None,
    webhook_adapter: WebhookAdapter | None = None,
    email_client: EmailClient | None = None,
    calendar_client: CalendarClient | None = None,
    webhook_client: WebhookClient | None = None,
    configs: dict[str, AdapterConfig] | None = None,
) -> list[ToolDefinition]:
    configs = configs or {}
    email = email_adapter or EmailAdapter(configs.get("email"), email_client)
    calendar = calendar_adapter or CalendarAdapter(configs.get("calendar"), calendar_client)
    webhook = webhook_adapter or WebhookAdapter(configs.get("webhook"), webhook_client)

    return [
        ToolDefinition(
            name="external.email.send",
            description="Send an email through the configured email adapter. Requires an injected email client for approved live execution.",
            input_schema={
                "type": "object",
                "required": ["to", "subject", "body"],
                "properties": {
                    "to": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                },
            },
            output_schema={},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="ExternalServices",
            supports_dry_run=True,
            requires_authorized_path=False,
            execute=lambda args, context: email.execute("send_email", _with_context_defaults(args, context)),
            search_hint="email send message external service adapter",
            concurrency_key="external.email",
            trust_tier="adapter",
            fast_path_eligible=False,
            effects=["send"],
            resource_kinds=["external_service"],
            external_network=True,
        ),
        ToolDefinition(
            name="external.calendar.create_event",
            description="Create a calendar event through the configured calendar adapter. Requires an injected calendar client for approved live execution.",
            input_schema={
                "type": "object",
                "required": ["title", "start", "end"],
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "dry_run": {"type": "boolean"},
                },
            },
            output_schema={},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="ExternalServices",
            supports_dry_run=True,
            requires_authorized_path=False,
            execute=lambda args, context: calendar.execute("create_event", _with_context_defaults(args, context)),
            search_hint="calendar create event meeting external service adapter",
            concurrency_key="external.calendar",
            trust_tier="adapter",
            fast_path_eligible=False,
            effects=["write", "send"],
            resource_kinds=["external_service"],
            external_network=True,
        ),
        ToolDefinition(
            name="external.webhook.post",
            description="Post a JSON payload through the configured webhook adapter. Requires an injected webhook client for approved live execution.",
            input_schema={
                "type": "object",
                "required": ["payload"],
                "properties": {
                    "url": {"type": "string"},
                    "payload": {"type": "object"},
                    "headers": {"type": "object"},
                    "dry_run": {"type": "boolean"},
                },
            },
            output_schema={},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="ExternalServices",
            supports_dry_run=True,
            requires_authorized_path=False,
            execute=lambda args, context: webhook.execute("post_webhook", _with_context_defaults(args, context)),
            search_hint="webhook post json external service adapter",
            concurrency_key="external.webhook",
            trust_tier="adapter",
            fast_path_eligible=False,
            effects=["external_post", "send"],
            resource_kinds=["external_service"],
            external_network=True,
            sensitive_arg_keys=["headers", "payload"],
        ),
    ]


def register(registry) -> None:
    for definition in create_adapter_tool_definitions():
        registry.register(definition)


def _with_context_defaults(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    payload = dict(args)
    if "dry_run" not in payload and "dry_run" in context:
        payload["dry_run"] = bool(context["dry_run"])
    return payload
