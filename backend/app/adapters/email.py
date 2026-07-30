from __future__ import annotations

import re
from typing import Any, Protocol

from app.adapters.base import AdapterBase, AdapterConfig, AdapterResult


class EmailClient(Protocol):
    def send_message(self, message: dict[str, Any]) -> dict[str, Any]: ...


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
    # Header fields must never contain CR/LF: a newline in subject/to/from/cc/bcc
    # lets an attacker (via injected model output or untrusted document content)
    # inject extra SMTP headers (e.g. a hidden Bcc that also evades the
    # run-budget recipient/domain binding). The body may legitimately contain
    # newlines and is not checked here.
    for field in ("subject", "from"):
        if _contains_crlf(payload.get(field)):
            return f"Email '{field}' must not contain line breaks."
    for field in ("to", "cc", "bcc"):
        for recipient in _as_recipient_list(payload.get(field)):
            if _contains_crlf(recipient):
                return f"Email '{field}' recipient must not contain line breaks."
            if recipient and not _looks_like_email(recipient):
                return f"Email '{field}' contains an invalid recipient address."
    return ""


_EMAIL_ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _contains_crlf(value: Any) -> bool:
    return isinstance(value, str) and ("\r" in value or "\n" in value)


def _as_recipient_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value]
    return [str(value).strip()]


def _looks_like_email(value: str) -> bool:
    return bool(_EMAIL_ADDRESS_RE.match(value.strip()))


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
