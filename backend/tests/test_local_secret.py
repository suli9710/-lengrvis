from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.security import local_secret
from app.security.local_secret import (
    LOCAL_SECRET_DPAPI_PREFIX,
    LOCAL_SECRET_KEYRING_PREFIX,
    dpapi_available,
    load_or_create_local_secret,
    read_local_secret,
)


def test_create_and_reload_roundtrip(tmp_path: Path):
    secret_path = tmp_path / "unit.secret"

    first = load_or_create_local_secret(secret_path, unavailable_message="unavailable")
    second = load_or_create_local_secret(secret_path, unavailable_message="unavailable")

    assert first
    assert first == second
    assert read_local_secret(secret_path) == first


def test_generated_secret_is_not_stored_in_plaintext_when_secure_backend_available(monkeypatch, tmp_path: Path):
    secret_path = tmp_path / "unit.secret"
    keyring_values: dict[str, str] = {}

    if not dpapi_available():
        monkeypatch.delenv("LENGRVIS_TEST", raising=False)
        monkeypatch.setattr(local_secret, "keyring_available", lambda: True)

        def fake_store(path: Path, value: str) -> str:
            account = f"fake:{path.name}"
            keyring_values[account] = value
            return LOCAL_SECRET_KEYRING_PREFIX + account

        monkeypatch.setattr(local_secret, "_keyring_store", fake_store)
        monkeypatch.setattr(local_secret, "_keyring_read", lambda stored: keyring_values[stored.split(":", 1)[1]])

    value = load_or_create_local_secret(secret_path, unavailable_message="unavailable")
    stored = secret_path.read_text(encoding="utf-8").strip()

    if dpapi_available():
        assert stored.startswith(LOCAL_SECRET_DPAPI_PREFIX)
    else:
        assert stored.startswith(LOCAL_SECRET_KEYRING_PREFIX)
    assert value not in stored
    assert read_local_secret(secret_path) == value


def test_keyring_reference_reloads_original_secret(monkeypatch, tmp_path: Path):
    secret_path = tmp_path / "unit.secret"
    keyring_values: dict[str, str] = {}

    monkeypatch.setattr(local_secret, "dpapi_available", lambda: False)
    monkeypatch.setattr(local_secret, "keyring_available", lambda: True)

    def fake_store(path: Path, value: str) -> str:
        account = f"fake:{path.name}"
        keyring_values[account] = value
        return LOCAL_SECRET_KEYRING_PREFIX + account

    monkeypatch.setattr(local_secret, "_keyring_store", fake_store)
    monkeypatch.setattr(local_secret, "_keyring_read", lambda stored: keyring_values[stored.split(":", 1)[1]])

    first = load_or_create_local_secret(secret_path, unavailable_message="unavailable")
    stored = secret_path.read_text(encoding="utf-8").strip()
    second = load_or_create_local_secret(secret_path, unavailable_message="unavailable")

    assert first == second
    assert stored == LOCAL_SECRET_KEYRING_PREFIX + "fake:unit.secret"
    assert secret_path.read_text(encoding="utf-8").strip() == stored


def test_non_windows_without_keyring_fails_closed_outside_test(monkeypatch, tmp_path: Path):
    if dpapi_available():
        pytest.skip("DPAPI is the secure backend on Windows")
    secret_path = tmp_path / "unit.secret"
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", raising=False)
    monkeypatch.setattr(local_secret, "keyring_available", lambda: False)

    with pytest.raises(RuntimeError):
        load_or_create_local_secret(secret_path, unavailable_message="unavailable")

    assert not secret_path.exists()


def test_lengrvis_test_alone_does_not_allow_plaintext_fallback(monkeypatch, tmp_path: Path):
    if dpapi_available():
        pytest.skip("DPAPI is the secure backend on Windows")
    secret_path = tmp_path / "unit.secret"
    monkeypatch.setenv("LENGRVIS_TEST", "1")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", raising=False)
    monkeypatch.setattr(local_secret, "keyring_available", lambda: False)

    with pytest.raises(RuntimeError):
        load_or_create_local_secret(secret_path, unavailable_message="unavailable")

    assert not secret_path.exists()


def test_explicit_insecure_dev_fallback_keeps_plaintext_on_non_windows(monkeypatch, tmp_path: Path):
    if dpapi_available():
        pytest.skip("DPAPI is the secure backend on Windows")
    secret_path = tmp_path / "unit.secret"
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")
    monkeypatch.setattr(local_secret, "keyring_available", lambda: False)

    value = load_or_create_local_secret(secret_path, unavailable_message="unavailable")

    assert secret_path.read_text(encoding="utf-8").strip() == value
    assert read_local_secret(secret_path) == value


def test_legacy_plaintext_secret_is_kept_and_migrated(tmp_path: Path):
    secret_path = tmp_path / "unit.secret"
    secret_path.write_text("legacy-secret", encoding="utf-8")

    value = load_or_create_local_secret(secret_path, unavailable_message="unavailable")

    assert value == "legacy-secret"
    stored = secret_path.read_text(encoding="utf-8").strip()
    if dpapi_available():
        assert stored.startswith(LOCAL_SECRET_DPAPI_PREFIX)
    assert read_local_secret(secret_path) == "legacy-secret"
    assert load_or_create_local_secret(secret_path, unavailable_message="unavailable") == "legacy-secret"


def test_legacy_plaintext_secret_migrates_to_keyring_when_available(monkeypatch, tmp_path: Path):
    if dpapi_available():
        pytest.skip("DPAPI migration is covered on Windows")
    secret_path = tmp_path / "unit.secret"
    secret_path.write_text("legacy-secret", encoding="utf-8")
    keyring_values: dict[str, str] = {}
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    monkeypatch.setattr(local_secret, "keyring_available", lambda: True)

    def fake_store(path: Path, value: str) -> str:
        account = f"fake:{path.name}"
        keyring_values[account] = value
        return LOCAL_SECRET_KEYRING_PREFIX + account

    monkeypatch.setattr(local_secret, "_keyring_store", fake_store)
    monkeypatch.setattr(local_secret, "_keyring_read", lambda stored: keyring_values[stored.split(":", 1)[1]])

    assert load_or_create_local_secret(secret_path, unavailable_message="unavailable") == "legacy-secret"
    stored = secret_path.read_text(encoding="utf-8").strip()
    assert stored.startswith(LOCAL_SECRET_KEYRING_PREFIX)
    assert "legacy-secret" not in stored
    assert read_local_secret(secret_path) == "legacy-secret"


def test_read_local_secret_missing_file_returns_empty(tmp_path: Path):
    assert read_local_secret(tmp_path / "missing.secret") == ""


def test_write_leaves_no_temp_file_and_survives_stale_temp(tmp_path: Path):
    secret_path = tmp_path / "unit.secret"
    stale = tmp_path / "unit.secret.tmp"
    stale.write_text("stale", encoding="utf-8")

    value = load_or_create_local_secret(secret_path, unavailable_message="unavailable")

    assert value
    assert read_local_secret(secret_path) == value
    assert not stale.exists()


def test_concurrent_create_returns_single_secret(monkeypatch, tmp_path: Path):
    secret_path = tmp_path / "unit.secret"
    monkeypatch.setattr(local_secret, "dpapi_available", lambda: False)
    monkeypatch.setattr(local_secret, "keyring_available", lambda: False)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    def load(_: int) -> str:
        return load_or_create_local_secret(secret_path, unavailable_message="unavailable")

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(load, range(24)))

    assert len(set(values)) == 1
    assert read_local_secret(secret_path) == values[0]
    assert not list(tmp_path.glob("*.tmp"))
