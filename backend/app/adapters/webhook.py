from __future__ import annotations

from typing import Any, Protocol

from app.adapters.base import AdapterBase, AdapterConfig, AdapterResult
from app.core.outbound_url import pin_outbound_http_url, validate_outbound_http_url_preview


class WebhookClient(Protocol):
    def post(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class WebhookAdapter(AdapterBase):
    def __init__(self, config: AdapterConfig | None = None, client: WebhookClient | None = None) -> None:
        super().__init__(config or AdapterConfig(service_name="webhook"))
        self.client = client

    def connect(self) -> AdapterResult:
        disabled = self._disabled_result()
        if disabled is not None:
            return disabled
        if self.client is None and not (self.config.dry_run or self.config.test_mode):
            return {
                "ok": False,
                "adapter": self.config.service_name,
                "error": "Webhook client is not configured.",
            }
        self._connected = True
        return {"ok": True, "adapter": self.config.service_name, "connected": True}

    def execute(self, operation: str, payload: dict[str, Any]) -> AdapterResult:
        if operation != "post_webhook":
            return {"ok": False, "adapter": self.config.service_name, "error": f"Unsupported operation: {operation}"}
        url = str(payload.get("url") or self.config.base_url).strip()
        if not url:
            return {"ok": False, "adapter": self.config.service_name, "error": "Webhook 'url' is required."}
        body = payload.get("payload")
        if not isinstance(body, dict):
            return {"ok": False, "adapter": self.config.service_name, "error": "Webhook 'payload' must be an object."}
        headers = {str(key): str(value) for key, value in (payload.get("headers") or {}).items()}
        timeout = float(payload.get("timeout_seconds") or self.config.timeout_seconds)
        if self._dry_run_enabled(payload):
            try:
                preview_url = validate_outbound_http_url_preview(url, allow_private=False)
            except ValueError as exc:
                return {"ok": False, "adapter": self.config.service_name, "error": str(exc)}
            return {
                "ok": True,
                "adapter": self.config.service_name,
                "dry_run": True,
                "request": {
                    "url": preview_url,
                    "payload": _redact_sensitive_values(body),
                    "headers": _redact_headers(headers),
                    "timeout_seconds": timeout,
                },
                "diff_preview": [{"action": "post_webhook", "url": preview_url}],
            }
        try:
            pinned = pin_outbound_http_url(url, allow_private=False)
        except ValueError as exc:
            return {"ok": False, "adapter": self.config.service_name, "error": str(exc)}
        connected_error = self._ensure_connected()
        if connected_error is not None:
            return connected_error
        if self.client is None:
            return {"ok": False, "adapter": self.config.service_name, "error": "Webhook client is not configured."}
        result = self.client.post(pinned.url, body, _merge_pinned_headers(pinned.headers, headers), timeout)
        return {"ok": bool(result.get("ok", True)), "adapter": self.config.service_name, **result}

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


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive_terms = ("authorization", "token", "api-key", "apikey", "secret", "cookie")
    return {
        key: "***" if any(term in key.lower() for term in sensitive_terms) else value for key, value in headers.items()
    }


def _merge_pinned_headers(pinned_headers: dict[str, str], caller_headers: dict[str, str]) -> dict[str, str]:
    merged = dict(pinned_headers)
    pinned_names = {key.lower() for key in pinned_headers}
    for key, value in caller_headers.items():
        if key.lower() == "host" and "host" in pinned_names:
            continue
        merged[key] = value
    return merged


def _redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if _is_sensitive_key(str(key)) else _redact_sensitive_values(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    sensitive_terms = (
        "authorization",
        "token",
        "api_key",
        "apikey",
        "secret",
        "cookie",
        "password",
        "credential",
        "credentials",
    )
    normalized = key.lower().replace("-", "_")
    return any(term in normalized for term in sensitive_terms)
