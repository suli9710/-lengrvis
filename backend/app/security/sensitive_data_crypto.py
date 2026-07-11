"""Authenticated encryption for high-sensitivity local payloads.

The content-encryption key is generated per installation and stored through
``local_secret``. On the supported Windows release target that means the key
file contains only a DPAPI-wrapped value. Pytest may use the existing
controlled plaintext-key fallback so encryption behavior remains testable on
non-Windows CI; production non-Windows use fails closed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_env
from app.security.local_secret import ALLOW_INSECURE_LOCAL_SECRETS_ENV, load_or_create_local_secret

ENCRYPTED_PAYLOAD_PREFIX = b"lengrvis:aesgcm:v1\x00"
SENSITIVE_DATA_KEY_FILE = "high_sensitive_data.key"  # noqa: S105 - filename, not a credential.
SENSITIVE_DATA_KEY_DIR = "secrets"
TEST_SENSITIVE_DATA_FALLBACK_ENV = "LENGRVIS_ALLOW_TEST_SENSITIVE_DATA_KEY"
_NONCE_BYTES = 12
_KEY_HEX_LENGTH = 64


class SensitiveDataEncryptionError(RuntimeError):
    """Raised when local high-sensitivity data cannot be encrypted safely."""


class SensitiveDataDecryptionError(RuntimeError):
    """Raised when an encrypted local payload fails authentication."""


def encrypt_sensitive_bytes(
    plaintext: bytes,
    *,
    purpose: str,
    binding: Mapping[str, str],
    data_dir: str | Path,
) -> bytes:
    if not plaintext:
        raise ValueError("Sensitive payload must not be empty.")
    key = _load_content_key(data_dir)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _associated_data(purpose, binding))
    return ENCRYPTED_PAYLOAD_PREFIX + nonce + ciphertext


def decrypt_sensitive_bytes(
    envelope: bytes,
    *,
    purpose: str,
    binding: Mapping[str, str],
    data_dir: str | Path,
) -> bytes:
    if not is_encrypted_payload(envelope):
        raise SensitiveDataDecryptionError("Local sensitive payload is not encrypted.")
    payload = envelope[len(ENCRYPTED_PAYLOAD_PREFIX) :]
    if len(payload) <= _NONCE_BYTES:
        raise SensitiveDataDecryptionError("Encrypted local data is truncated.")
    key = _load_content_key(data_dir)
    nonce = payload[:_NONCE_BYTES]
    ciphertext = payload[_NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _associated_data(purpose, binding))
    except InvalidTag as exc:
        raise SensitiveDataDecryptionError("Encrypted local data failed integrity validation.") from exc


def is_encrypted_payload(value: bytes) -> bool:
    return bytes(value).startswith(ENCRYPTED_PAYLOAD_PREFIX)


def sensitive_data_key_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / SENSITIVE_DATA_KEY_DIR / SENSITIVE_DATA_KEY_FILE


def _load_content_key(data_dir: str | Path) -> bytes:
    _require_supported_key_storage()
    path = sensitive_data_key_path(data_dir)
    try:
        encoded = load_or_create_local_secret(
            path,
            unavailable_message="High-sensitivity local data encryption key is unavailable.",
        )
        if len(encoded) != _KEY_HEX_LENGTH:
            raise ValueError("unexpected key length")
        key = bytes.fromhex(encoded)
    except (RuntimeError, ValueError) as exc:
        raise SensitiveDataEncryptionError("High-sensitivity local data encryption key is unavailable.") from exc
    if len(key) != 32:
        raise SensitiveDataEncryptionError("High-sensitivity local data encryption key is invalid.")
    return key


def _require_supported_key_storage() -> None:
    if os.name == "nt" or _test_fallback_allowed():
        return
    raise SensitiveDataEncryptionError(
        "High-sensitivity local data encryption requires Windows DPAPI outside controlled tests."
    )


def _test_fallback_allowed() -> bool:
    if str(get_env("PYTEST_CURRENT_TEST") or "").strip():
        return True
    test_mode = _env_flag("LENGRVIS_TEST")
    explicit_fallback = _env_flag(TEST_SENSITIVE_DATA_FALLBACK_ENV)
    insecure_secret_storage = _env_flag(ALLOW_INSECURE_LOCAL_SECRETS_ENV)
    return test_mode and explicit_fallback and insecure_secret_storage


def _associated_data(purpose: str, binding: Mapping[str, str]) -> bytes:
    normalized_purpose = str(purpose or "").strip()
    if not normalized_purpose:
        raise ValueError("Sensitive payload purpose is required.")
    normalized_binding = {str(key): str(value) for key, value in sorted(binding.items())}
    if not normalized_binding or any(not value for value in normalized_binding.values()):
        raise ValueError("Sensitive payload binding must contain non-empty values.")
    return json.dumps(
        {
            "version": 1,
            "purpose": normalized_purpose,
            "binding": normalized_binding,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _env_flag(name: str) -> bool:
    return str(get_env(name) or "").strip().lower() in {"1", "true", "yes", "on"}
