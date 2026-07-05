from __future__ import annotations

from typing import Any


def mobile_device_trust_metadata() -> dict[str, Any]:
    return {
        "attestation_verified": False,
        "attestation_status": "not_verified",
        "attestation_provider": "none",
        "trust_basis": "pairing_code_tls",
        "hardware_backed": False,
        "message": (
            "Device identity is not hardware-attested; trust is limited to the pairing code, "
            "paired session, and LAN TLS/pinning state."
        ),
    }


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    if isinstance(value, list | tuple | set):
        result: list[str] = []
        for item in value:
            result.extend(_text_list(item))
        return result
    text = _text(value)
    return [text] if text else []


def _text(value: Any) -> str:
    return str(value or "").strip()
