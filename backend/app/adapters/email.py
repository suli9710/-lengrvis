from __future__ import annotations

from typing import Any, Protocol

from app.adapters.base import AdapterBase, AdapterConfig, AdapterResult


class EmailClient(Protocol):
    def send_message(self, message: dict[str, Any]) -> dict[str, Any]:
        ...


class EmailAdapter(AdapterBase):
    def __init__(self, config: AdapterConfig | None = None, client: EmailClient | None = None) -> None:
        super().__init__(config or AdapterConfig(service_name="email"))
        self.client = client

    def connect(self) -> AdapterResult:
        disabled = self._disabled_result()
        if disabled is not None:
            return disabled
        if self.client is None and not (self.config.dry_run or self.config.test_mode):
            return {
                "ok": False,
                "adapter": self.config.service_name,
                "error": "Email client is not configured.",
            }
        self._connected = True
        return {"ok": True, "adapter": self.config.service_name, "connected": True}

    def execute(self, operation: str, payload: dict[str, Any]) -> AdapterResult:
        if operation != "send_email":
            return {"ok": False, "adapter": self.config.service_name, "error": f"Unsupported operation: {operation}"}
        validation_error = _validate_email_payload(payload)
        if validation_error:
            return {"ok": False, "adapter": self.config.service_name, "error": validation_error}
        message = _build_message(payload, self.config)
        if self._dry_run_enabled(payload):
            return {
                "ok": True,
                "adapter": self.config.service_name,
                "dry_run": True,
                "message": message,
                "diff_preview": [{"action": "send_email", "to": message["to"], "subject": message["subject"]}],
            }
        connected_error = self._ensure_connected()
        if connected_error is not None:
            return connected_error
        if self.client is None:
            return {"ok": False, "adapter": self.config.service_name, "error": "Email client is not configured."}
        result = self.client.send_message(message)
        return {"ok": bool(result.get("ok", True)), "adapter": self.config.service_name, "message": message, **result}

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


def _validate_email_payload(payload: dict[str, Any]) -> str:
    if not payload.get("to"):
        return "Email 'to' is required."
    if not payload.get("subject"):
        return "Email 'subject' is required."
    if not payload.get("body"):
        return "Email 'body' is required."
    return ""


def _build_message(payload: dict[str, Any], config: AdapterConfig) -> dict[str, Any]:
    to_value = payload["to"]
    recipients = [str(item).strip() for item in to_value] if isinstance(to_value, list) else [str(to_value).strip()]
    return {
        "to": [item for item in recipients if item],
        "subject": str(payload["subject"]),
        "body": str(payload["body"]),
        "from": str(payload.get("from") or config.default_sender),
        "cc": payload.get("cc") or [],
        "bcc": payload.get("bcc") or [],
        "metadata": payload.get("metadata") or {},
    }
