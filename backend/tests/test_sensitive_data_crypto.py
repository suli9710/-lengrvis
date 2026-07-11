from __future__ import annotations

from pathlib import Path

import pytest

from app.security import sensitive_data_crypto
from app.security.local_secret import LOCAL_SECRET_DPAPI_PREFIX, dpapi_available
from app.security.sensitive_data_crypto import (
    ENCRYPTED_PAYLOAD_PREFIX,
    SensitiveDataDecryptionError,
    SensitiveDataEncryptionError,
    decrypt_sensitive_bytes,
    encrypt_sensitive_bytes,
    sensitive_data_key_path,
)


def test_sensitive_bytes_roundtrip_without_plaintext_at_rest(tmp_path: Path):
    plaintext = b"private screenshot bytes token=recording-secret"
    binding = {"task_id": "task_1", "recording_id": "rec_1"}

    envelope = encrypt_sensitive_bytes(
        plaintext,
        purpose="test_payload",
        binding=binding,
        data_dir=tmp_path,
    )

    assert envelope.startswith(ENCRYPTED_PAYLOAD_PREFIX)
    assert plaintext not in envelope
    assert (
        decrypt_sensitive_bytes(
            envelope,
            purpose="test_payload",
            binding=binding,
            data_dir=tmp_path,
        )
        == plaintext
    )
    stored_key = sensitive_data_key_path(tmp_path).read_text(encoding="utf-8")
    assert "private screenshot" not in stored_key
    if dpapi_available():
        assert stored_key.startswith(LOCAL_SECRET_DPAPI_PREFIX)


def test_sensitive_bytes_reject_tampering_and_binding_changes(tmp_path: Path):
    binding = {"task_id": "task_1", "recording_id": "rec_1"}
    envelope = encrypt_sensitive_bytes(
        b"sensitive",
        purpose="test_payload",
        binding=binding,
        data_dir=tmp_path,
    )

    tampered = envelope[:-1] + bytes([envelope[-1] ^ 1])
    with pytest.raises(SensitiveDataDecryptionError, match="integrity validation"):
        decrypt_sensitive_bytes(
            tampered,
            purpose="test_payload",
            binding=binding,
            data_dir=tmp_path,
        )

    with pytest.raises(SensitiveDataDecryptionError, match="integrity validation"):
        decrypt_sensitive_bytes(
            envelope,
            purpose="test_payload",
            binding={"task_id": "task_2", "recording_id": "rec_1"},
            data_dir=tmp_path,
        )


def test_production_non_windows_key_storage_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    key_path = sensitive_data_key_path(tmp_path)
    monkeypatch.setattr(sensitive_data_crypto.os, "name", "posix")
    monkeypatch.setattr(sensitive_data_crypto, "_test_fallback_allowed", lambda: False)

    with pytest.raises(SensitiveDataEncryptionError, match="requires Windows DPAPI"):
        encrypt_sensitive_bytes(
            b"sensitive",
            purpose="test_payload",
            binding={"recording_id": "rec_1"},
            data_dir=tmp_path,
        )

    assert not key_path.exists()
