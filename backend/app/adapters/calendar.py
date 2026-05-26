from __future__ import annotations

from typing import Any, Protocol

from app.adapters.base import AdapterBase, AdapterConfig, AdapterResult


class CalendarClient(Protocol):
    def create_event(self, event: dict[str, Any]) -> dict[str, Any]:
        ...


class CalendarAdapter(AdapterBase):
    def __init__(self, config: AdapterConfig | None = None, client: CalendarClient | None = None) -> None:
        super().__init__(config or AdapterConfig(service_name="calendar"))
        self.client = client

    def connect(self) -> AdapterResult:
        disabled = self._disabled_result()
        if disabled is not None:
            return disabled
        if self.client is None and not (self.config.dry_run or self.config.test_mode):
            return {
                "ok": False,
                "adapter": self.config.service_name,
                "error": "Calendar client is not configured.",
            }
        self._connected = True
        return {"ok": True, "adapter": self.config.service_name, "connected": True}

    def execute(self, operation: str, payload: dict[str, Any]) -> AdapterResult:
        if operation != "create_event":
            return {"ok": False, "adapter": self.config.service_name, "error": f"Unsupported operation: {operation}"}
        validation_error = _validate_event_payload(payload)
        if validation_error:
            return {"ok": False, "adapter": self.config.service_name, "error": validation_error}
        event = _build_event(payload)
        if self._dry_run_enabled(payload):
            return {
                "ok": True,
                "adapter": self.config.service_name,
                "dry_run": True,
                "event": event,
                "diff_preview": [{"action": "create_event", "title": event["title"], "start": event["start"]}],
            }
        connected_error = self._ensure_connected()
        if connected_error is not None:
            return connected_error
        if self.client is None:
            return {"ok": False, "adapter": self.config.service_name, "error": "Calendar client is not configured."}
        result = self.client.create_event(event)
        return {"ok": bool(result.get("ok", True)), "adapter": self.config.service_name, "event": event, **result}

    def health_check(self) -> AdapterResult:
        disabled = self._disabled_result()
        if disabled is not None:
            return disabled
        return {
            "ok": True,
            "adapter": self.config.service_name,
            "configured": self.client is not None or self.config.dry_run or self.config.test_mode,
            "dry_run": self.config.dry_run,
            "test_mode": self.config.test_mode,
        }


def _validate_event_payload(payload: dict[str, Any]) -> str:
    if not payload.get("title"):
        return "Calendar event 'title' is required."
    if not payload.get("start"):
        return "Calendar event 'start' is required."
    if not payload.get("end"):
        return "Calendar event 'end' is required."
    return ""


def _build_event(payload: dict[str, Any]) -> dict[str, Any]:
    attendees = payload.get("attendees") or []
    if isinstance(attendees, str):
        attendees = [attendees]
    return {
        "title": str(payload["title"]),
        "start": str(payload["start"]),
        "end": str(payload["end"]),
        "timezone": str(payload.get("timezone") or "UTC"),
        "attendees": [str(item).strip() for item in attendees if str(item).strip()],
        "location": str(payload.get("location") or ""),
        "description": str(payload.get("description") or ""),
        "metadata": payload.get("metadata") or {},
    }
