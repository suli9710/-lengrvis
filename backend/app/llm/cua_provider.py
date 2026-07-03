from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.config import AppSettings
from app.core.outbound_url import is_local_base_url, pin_outbound_http_url, validate_outbound_http_url
from app.llm.openai_compatible import normalize_openai_base_url
from app.policy.redaction import redact_text, redact_value

DEFAULT_CUA_MODEL = "computer-use-preview"
OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"
CUA_BROWSER_ENVIRONMENT = "browser"
MAX_CUA_SCREENSHOT_BYTES = 8 * 1024 * 1024
MAX_CUA_SCREENSHOT_BASE64_CHARS = ((MAX_CUA_SCREENSHOT_BYTES + 2) // 3) * 4
_ALLOWED_CUA_SCREENSHOT_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}


class CUAUnavailable(RuntimeError):
    """Raised when computer-use is not configured or not supported by the provider."""


class _AsyncPostClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        extensions: dict[str, Any] | None = None,
    ) -> httpx.Response: ...


@dataclass(slots=True)
class CUAProvider:
    settings: AppSettings
    model: str = DEFAULT_CUA_MODEL
    source: str = "openai_compatible"
    client_factory: Any = httpx.AsyncClient

    async def run_step(
        self,
        *,
        instruction: str,
        screenshot: str | None = None,
        previous_response_id: str | None = None,
        acknowledged_safety_checks: list[dict[str, Any]] | None = None,
        environment: str = "browser",
    ) -> dict[str, Any]:
        if previous_response_id:
            return _denied_result("CUA previous_response_id cannot be supplied to run_step.", self.source, self.model)
        if acknowledged_safety_checks:
            return _denied_result(
                "CUA safety checks cannot be acknowledged through run_step.", self.source, self.model
            )
        environment_name = _normalize_cua_environment(environment)
        if environment_name != CUA_BROWSER_ENVIRONMENT:
            return _unavailable_result(
                "Browser CUA only supports the browser environment.", self.source, self.model
            )
        if not self.settings.api_key:
            return _unavailable_result(
                "CUA provider is unavailable because no API key is configured.", self.source, self.model
            )
        try:
            screenshot_url = validate_cua_screenshot_data_url(screenshot)
        except ValueError as exc:
            return _unavailable_result(str(exc), self.source, self.model)
        payload = self._payload(
            instruction=instruction,
            screenshot=screenshot_url,
            environment=environment_name,
        )
        try:
            async with self.client_factory(timeout=self.settings.timeout) as client:
                # Re-pin per call (DNS-rebinding TOCTOU): connect to the IP that
                # passed SSRF validation; Host/SNI keep the real hostname so TLS
                # certificate verification is unchanged.
                endpoint = self._responses_endpoint()
                pinned = pin_outbound_http_url(endpoint, allow_private=self._allow_private_base(endpoint))
                response = await client.post(
                    pinned.url,
                    headers={**self._headers(), **pinned.headers},
                    json=payload,
                    extensions=dict(pinned.extensions),
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return _unavailable_result(
                f"CUA provider request failed or is unsupported: {_safe_error_message(exc)}",
                self.source,
                self.model,
            )
        return self._normalize_response(data)

    def _payload(
        self,
        *,
        instruction: str,
        screenshot: str | None,
        environment: str,
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": instruction}]
        if screenshot:
            content.append({"type": "input_image", "image_url": screenshot})
        payload: dict[str, Any] = {
            "model": self.model,
            "tools": [{"type": "computer_use_preview", "environment": environment}],
            "input": [{"role": "user", "content": content}],
            "store": not self.settings.disable_response_storage,
        }
        return payload

    def _normalize_response(self, data: dict[str, Any]) -> dict[str, Any]:
        pending = _pending_safety_checks(data)
        if pending:
            return {
                "ok": False,
                "status": "requires_approval",
                "paused": True,
                "provider": self.source,
                "model": self.model,
                "response_id": data.get("id"),
                "pending_safety_checks": _redact_safety_checks(pending),
                "reason": "CUA returned pending safety checks; user approval is required before continuing.",
            }
        if data.get("error"):
            return _unavailable_result(
                f"CUA provider returned an error: {_error_message(data['error'])}", self.source, self.model
            )
        status = str(data.get("status") or "")
        if status in {"failed", "cancelled", "incomplete"}:
            detail = data.get("incomplete_details") or data.get("error") or status
            return _unavailable_result(
                f"CUA provider returned terminal status: {_safe_detail_message(detail)}",
                self.source,
                self.model,
            )
        return {
            "ok": True,
            "status": status or "completed",
            "provider": self.source,
            "model": self.model,
            "response_id": data.get("id"),
            "output": redact_value(data.get("output") or []),
        }

    def _api_base_url(self) -> str:
        base = normalize_openai_base_url(self.settings.base_url)
        if base:
            # SSRF guard: reject CUA "responses" calls aimed at loopback /
            # private / link-local / cloud-metadata hosts. DNS resolution here
            # (mirrors openai_compatible) also blocks hostnames that resolve to
            # internal addresses; pinning at request time closes the rebinding
            # TOCTOU window.
            validate_outbound_http_url(base, allow_private=self._allow_private_base(base))
        return base

    def _allow_private_base(self, base: str) -> bool:
        from app.llm.registry import LOCAL_PROVIDERS

        return is_local_base_url(base) and self.settings.provider_name.lower() in LOCAL_PROVIDERS

    def _responses_endpoint(self) -> str:
        return f"{self._api_base_url()}/responses"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.requires_openai_auth and self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers


async def resolve_cua_provider(
    settings: AppSettings,
    *,
    mode: str = "auto",
    client_factory: Any = httpx.AsyncClient,
) -> CUAProvider | dict[str, Any]:
    normalized_mode = str(mode or "auto").casefold()
    model = _configured_cua_model(settings)
    candidates = _candidate_settings(settings, normalized_mode)
    if not candidates:
        return _unavailable_result("CUA provider is not configured for the requested mode.", "none", model)

    last_reason = ""
    for source, candidate in candidates:
        if not candidate.api_key:
            last_reason = f"{source} has no API key configured."
            continue
        provider = CUAProvider(candidate, model=model, source=source, client_factory=client_factory)
        probe = await probe_cua_provider(provider)
        if probe.get("available"):
            return provider
        last_reason = str(probe.get("reason") or "CUA probe failed.")
    return _unavailable_result(last_reason or "CUA provider is unavailable or unsupported.", "auto", model)


async def probe_cua_provider(provider: CUAProvider) -> dict[str, Any]:
    payload = {
        "model": provider.model,
        "tools": [{"type": "computer_use_preview", "environment": CUA_BROWSER_ENVIRONMENT}],
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "probe computer-use availability"}]}],
        "max_output_tokens": 1,
        "store": False,
    }
    try:
        async with provider.client_factory(timeout=provider.settings.timeout) as client:
            endpoint = provider._responses_endpoint()
            pinned = pin_outbound_http_url(endpoint, allow_private=provider._allow_private_base(endpoint))
            response = await client.post(
                pinned.url,
                headers={**provider._headers(), **pinned.headers},
                json=payload,
                extensions=dict(pinned.extensions),
            )
            if response.status_code in {400, 404, 422}:
                return {
                    "available": False,
                    "reason": "Responses computer-use preview is not supported by this endpoint.",
                }
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "available": False,
            "reason": f"Responses computer-use preview probe failed: {_safe_error_message(exc)}",
        }
    if data.get("error"):
        return {"available": False, "reason": _error_message(data["error"])}
    return {"available": True, "provider": provider.source, "model": provider.model}


def _candidate_settings(settings: AppSettings, mode: str) -> list[tuple[str, AppSettings]]:
    official = _official_openai_settings(settings)
    if mode == "openai":
        return [("openai", official)] if official is not None else []
    if mode == "openai_compatible":
        return [("openai_compatible", _responses_settings(settings))]
    if mode != "auto":
        return []
    candidates = [("openai_compatible", _responses_settings(settings))]
    if official is not None and official != candidates[0][1]:
        candidates.append(("openai", official))
    return candidates


def _responses_settings(settings: AppSettings) -> AppSettings:
    return settings.model_copy(update={"wire_api": "responses"})


def _official_openai_settings(settings: AppSettings) -> AppSettings | None:
    if not settings.api_key or not _is_official_openai_base_url(settings.base_url):
        return None
    return settings.model_copy(
        update={
            "provider_name": "openai",
            "base_url": OFFICIAL_OPENAI_BASE_URL,
            "wire_api": "responses",
            "requires_openai_auth": True,
        }
    )


def _is_official_openai_base_url(base_url: str) -> bool:
    try:
        parsed = urlparse(normalize_openai_base_url(base_url))
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.openai.com"
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and parsed.path.rstrip("/") == "/v1"
        and not parsed.query
        and not parsed.fragment
    )


def _configured_cua_model(settings: AppSettings) -> str:
    for attr in ("cua_model", "computer_use_model"):
        value = getattr(settings, attr, "")
        if value:
            return str(value)
    return DEFAULT_CUA_MODEL


def _normalize_cua_environment(environment: Any) -> str:
    return str(environment or CUA_BROWSER_ENVIRONMENT).strip().casefold().replace("_", "-")


def validate_cua_screenshot_data_url(screenshot: str | None) -> str | None:
    value = str(screenshot or "").strip()
    if not value:
        return None
    prefix, separator, encoded = value.partition(",")
    if separator != "," or not prefix.casefold().startswith("data:image/"):
        raise ValueError("CUA screenshot must be an inline data:image/*;base64 payload.")
    header = prefix.split(":", 1)[1].casefold()
    parts = [part.strip() for part in header.split(";") if part.strip()]
    mime_type = parts[0] if parts else ""
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    if mime_type not in _ALLOWED_CUA_SCREENSHOT_MIME_TYPES or "base64" not in parts[1:]:
        raise ValueError("CUA screenshot must be a PNG, JPEG, or WebP base64 data URL.")
    if len(encoded) > MAX_CUA_SCREENSHOT_BASE64_CHARS:
        raise ValueError("CUA screenshot exceeds the 8 MB decoded payload limit.")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("CUA screenshot is not valid base64.") from exc
    if len(decoded) > MAX_CUA_SCREENSHOT_BYTES:
        raise ValueError("CUA screenshot exceeds the 8 MB decoded payload limit.")
    return value


def _pending_safety_checks(data: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("pending_safety_checks")
        if isinstance(raw, list):
            checks.extend(item for item in raw if isinstance(item, dict))
    return checks


def _redact_safety_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    redacted = redact_value(checks)
    if not isinstance(redacted, list):
        return []
    result: list[dict[str, Any]] = []
    for item in redacted:
        if not isinstance(item, dict):
            continue
        sanitized = dict(item)
        if "message" in sanitized:
            sanitized["message"] = "[REDACTED]"
        result.append(sanitized)
    return result


def _error_message(error: Any) -> str:
    if isinstance(error, dict):
        return redact_text(str(error.get("message") or error.get("type") or "provider error"))
    return redact_text(str(error))


def _safe_error_message(error: BaseException) -> str:
    return redact_text(str(error))


def _safe_detail_message(detail: Any) -> str:
    return redact_text(str(redact_value(detail)))


def _unavailable_result(reason: str, provider: str, model: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "unavailable",
        "degraded": True,
        "provider": provider,
        "model": model,
        "reason": reason,
    }


def _denied_result(reason: str, provider: str, model: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "denied",
        "provider": provider,
        "model": model,
        "reason": reason,
    }
