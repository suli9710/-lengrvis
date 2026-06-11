from __future__ import annotations

from pathlib import Path

from app.security.local_secret import (
    LOCAL_SECRET_DPAPI_PREFIX,
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


def test_generated_secret_is_not_stored_in_plaintext_when_dpapi_available(tmp_path: Path):
    secret_path = tmp_path / "unit.secret"

    value = load_or_create_local_secret(secret_path, unavailable_message="unavailable")
    stored = secret_path.read_text(encoding="utf-8").strip()

    if dpapi_available():
        assert stored.startswith(LOCAL_SECRET_DPAPI_PREFIX)
        assert value not in stored
    else:
        assert stored == value


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
