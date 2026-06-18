"""Shared storage helper for locally generated secrets.

Windows file permissions (``chmod``) do not restrict NTFS ACLs, so plaintext
secret files are readable by other local users. When Windows DPAPI is
available the secret is stored encrypted with a ``dpapi:`` prefix and bound
to the current user account. Plaintext files written by older versions are
migrated to the encrypted format on first read. On platforms without DPAPI
the secret stays plaintext with restrictive POSIX permissions (which work
there).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import time
from pathlib import Path

LOCAL_SECRET_DPAPI_PREFIX = "dpapi:"
_DPAPI_DESCRIPTION = "lengrvis-local-secret"
logger = logging.getLogger(__name__)


def dpapi_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32crypt  # type: ignore[import-not-found]  # noqa: F401
    except Exception:  # noqa: BLE001 - optional dependency probe.
        return False
    return True


def _dpapi_protect(value: str) -> str:
    import win32crypt  # type: ignore[import-not-found]

    blob = win32crypt.CryptProtectData(value.encode("utf-8"), _DPAPI_DESCRIPTION, None, None, None, 0)
    return LOCAL_SECRET_DPAPI_PREFIX + base64.b64encode(bytes(blob)).decode("ascii")


def _dpapi_unprotect(stored: str) -> str:
    import win32crypt  # type: ignore[import-not-found]

    blob = base64.b64decode(stored[len(LOCAL_SECRET_DPAPI_PREFIX) :])
    return bytes(win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]).decode("utf-8")


def _write_secret_file(path: Path, value: str) -> None:
    stored = _dpapi_protect(value) if dpapi_available() else value
    # Write to a sibling temp file created with O_EXCL and restrictive mode,
    # then replace atomically: the secret is never on disk with default
    # permissions, even briefly (the old write-then-chmod left a window).
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)  # clear stale temp from a crashed writer
    fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(stored)
        replace_attempts = 5 if os.name == "nt" else 1
        for attempt in range(replace_attempts):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == replace_attempts - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def read_local_secret(path: Path) -> str:
    """Return the decrypted secret stored at ``path`` ('' when absent/empty)."""
    if not path.exists():
        return ""
    stored = path.read_text(encoding="utf-8").strip()
    if not stored:
        return ""
    if stored.startswith(LOCAL_SECRET_DPAPI_PREFIX):
        try:
            return _dpapi_unprotect(stored)
        except Exception as exc:  # noqa: BLE001 - callers need a clear config failure.
            raise RuntimeError(f"Failed to decrypt local secret at {path} with Windows DPAPI.") from exc
    return stored


def load_or_create_local_secret(path: Path, *, unavailable_message: str) -> str:
    """Load the secret at ``path``, creating (and encrypting) it when missing.

    Plaintext files from older versions are migrated to the DPAPI format when
    encryption is available. Raises ``RuntimeError`` with
    ``unavailable_message`` when the secret cannot be read or persisted.
    """
    try:
        if path.exists():
            stored = path.read_text(encoding="utf-8").strip()
            if stored.startswith(LOCAL_SECRET_DPAPI_PREFIX):
                try:
                    return _dpapi_unprotect(stored)
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(unavailable_message) from exc
            if stored:
                if dpapi_available():
                    try:
                        _write_secret_file(path, stored)
                        logger.info("migrated plaintext local secret at %s to DPAPI storage", path)
                    except OSError as exc:
                        logger.debug("could not migrate local secret at %s: %s", path, exc)
                return stored
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        _write_secret_file(path, value)
        return value
    except RuntimeError:
        raise
    except OSError as exc:
        raise RuntimeError(unavailable_message) from exc
