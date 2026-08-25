from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

from app.tools import managed_backup_identity as identity_module
from app.tools.managed_backup_identity import (
    MANAGED_BACKUP_IDENTITY_KEYS,
    capture_managed_backup_identity,
    validate_managed_backup_identity,
)


@pytest.mark.parametrize("payload", [b"", b"managed backup payload"])
def test_capture_binds_content_and_same_descriptor_metadata(tmp_path: Path, payload: bytes) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(payload)

    identity = capture_managed_backup_identity(backup, expected_size=len(payload))

    assert set(identity) == MANAGED_BACKUP_IDENTITY_KEYS
    assert identity["schema"] == "managed-backup-identity/v3"
    assert identity["sha256"] == hashlib.sha256(payload).hexdigest()
    assert identity["size"] == len(payload)
    assert identity["object_id"].startswith("win32:" if sys.platform == "win32" else "posix:")
    assert validate_managed_backup_identity(identity) == identity


@pytest.mark.parametrize(
    "mutation",
    ["extra", "missing", "bool_number", "uppercase_digest", "legacy_schema", "bad_object_id", "wrong_platform"],
)
def test_validate_identity_rejects_noncanonical_evidence(tmp_path: Path, mutation: str) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"payload")
    identity = capture_managed_backup_identity(backup)

    if mutation == "extra":
        identity["unexpected"] = "field"
    elif mutation == "missing":
        identity.pop("change_time_ns")
    elif mutation == "bool_number":
        identity["size"] = True
    elif mutation == "uppercase_digest":
        identity["sha256"] = "A" * 64
    elif mutation == "legacy_schema":
        identity["schema"] = "managed-backup-identity/v2"
    elif mutation == "bad_object_id":
        identity["object_id"] = "not-an-object-id"
    elif sys.platform == "win32":
        identity["object_id"] = f"posix:{identity['device']}:{identity['inode']}"
    else:
        identity["object_id"] = "win32:0000000000000001:00000000000000000000000000000001"

    with pytest.raises(ValueError, match="Managed backup"):
        validate_managed_backup_identity(identity)


def test_validate_identity_rejects_inconsistent_posix_object_id(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX object IDs are rejected earlier on Windows.")
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"payload")
    identity = capture_managed_backup_identity(backup)
    identity["object_id"] = f"posix:{identity['device']}:{identity['inode'] + 1}"

    with pytest.raises(ValueError, match="inconsistent"):
        validate_managed_backup_identity(identity)


def test_validate_identity_accepts_signed_timestamps(tmp_path: Path) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"payload")
    identity = capture_managed_backup_identity(backup)
    identity["mtime_ns"] = -1_000_000_000
    identity["change_time_ns"] = -1

    assert validate_managed_backup_identity(identity) == identity


def test_capture_preserves_pre_epoch_mtime(tmp_path: Path) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"payload")
    os_mtime_ns = -1_000_000_000
    try:
        backup.touch()
        os.utime(backup, ns=(os_mtime_ns, os_mtime_ns))
    except (OSError, OverflowError) as exc:
        pytest.skip(f"filesystem does not support pre-epoch timestamps: {exc}")

    identity = capture_managed_backup_identity(backup)
    if identity["mtime_ns"] >= 0:
        pytest.skip("filesystem clamps pre-epoch timestamps")

    assert validate_managed_backup_identity(identity) == identity


@pytest.mark.skipif(sys.platform != "win32", reason="Windows FILE_BASIC_INFO contract")
def test_windows_capture_uses_change_time_not_tunneled_creation_time(tmp_path: Path) -> None:
    backup = tmp_path / "tunneled-name.bin"
    backup.write_bytes(b"same payload")
    before = capture_managed_backup_identity(backup)

    after = before
    for _attempt in range(5):
        backup.unlink()
        backup.write_bytes(b"same payload")
        after = capture_managed_backup_identity(backup)
        if after["change_time_ns"] != before["change_time_ns"]:
            break
        time.sleep(0.02)

    assert after["sha256"] == before["sha256"]
    assert after["size"] == before["size"]
    assert after["change_time_ns"] != before["change_time_ns"]


def test_expected_size_rejects_before_reading_and_closes_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"payload")
    monkeypatch.setattr(identity_module.os, "read", lambda *_args: pytest.fail("size mismatch must skip hashing"))

    with pytest.raises(OSError, match="size changed"):
        capture_managed_backup_identity(backup, expected_size=8)

    backup.unlink()


def test_capture_rejects_metadata_change_and_closes_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"payload")
    original_metadata = identity_module._descriptor_metadata
    calls = 0

    def changed_metadata(descriptor: int) -> dict:
        nonlocal calls
        calls += 1
        metadata = original_metadata(descriptor)
        if calls == 2:
            metadata["change_time_ns"] += 100
        return metadata

    monkeypatch.setattr(identity_module, "_descriptor_metadata", changed_metadata)
    with pytest.raises(OSError, match="changed while"):
        capture_managed_backup_identity(backup)

    backup.unlink()


def test_capture_rejects_short_read_and_closes_descriptor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"payload")
    monkeypatch.setattr(identity_module.os, "read", lambda *_args: b"")

    with pytest.raises(OSError, match="changed while"):
        capture_managed_backup_identity(backup)

    backup.unlink()


def test_abort_during_hash_closes_descriptor(tmp_path: Path) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"payload")
    calls = 0

    def abort() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("abort requested")

    with pytest.raises(RuntimeError, match="abort requested"):
        capture_managed_backup_identity(backup, abort_callback=abort)

    backup.unlink()


def test_capture_rejects_non_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises(OSError):
        capture_managed_backup_identity(directory)


def test_invalid_expected_size_is_rejected_before_open(tmp_path: Path) -> None:
    missing = tmp_path / "missing.bin"

    with pytest.raises(ValueError, match="expected size"):
        capture_managed_backup_identity(missing, expected_size=True)

    assert not missing.exists()
