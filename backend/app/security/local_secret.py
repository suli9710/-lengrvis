"""Shared storage helper for locally generated secrets.

Windows file permissions (``chmod``) do not restrict NTFS ACLs, so plaintext
secret files are readable by other local users. When Windows DPAPI is
available the secret is stored encrypted with a ``dpapi:`` prefix and bound
to the current user account. On macOS/Linux, secrets are stored in the system
keyring and the file contains only a ``keyring:`` lookup handle. Plaintext
files written by older versions are migrated to the secure backend when one is
available.

If neither DPAPI nor a system keyring is available, secret creation/loading is
fail-closed. Tests or local development may opt into the old plaintext behavior
with ``LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS=1``; production code should prefer
an explicit environment-provided secret instead of relying on that fallback.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import time
from hashlib import sha256
from pathlib import Path

LOCAL_SECRET_DPAPI_PREFIX = "dpapi:"  # noqa: S105 - marker prefix, not a credential.
LOCAL_SECRET_KEYRING_PREFIX = "keyring:"  # noqa: S105 - marker prefix, not a credential.
ALLOW_INSECURE_LOCAL_SECRETS_ENV = "LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS"
_DPAPI_DESCRIPTION = "lengrvis-local-secret"
_KEYRING_SERVICE = "lengrvis.local-secret"
logger = logging.getLogger(__name__)


def dpapi_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32crypt  # type: ignore[import-not-found]  # noqa: F401
    except Exception:  # noqa: BLE001 - optional dependency probe.
        return False
    return True


def keyring_available() -> bool:
    if os.name == "nt":
        return False
    try:
        import keyring  # type: ignore[import-not-found]

        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - optional dependency/backend probe.
        return False
    backend_module = backend.__class__.__module__.lower()
    return "keyring.backends.fail" not in backend_module


def _dpapi_protect(value: str) -> str:
    import win32crypt  # type: ignore[import-not-found]

    blob = win32crypt.CryptProtectData(value.encode("utf-8"), _DPAPI_DESCRIPTION, None, None, None, 0)
    return LOCAL_SECRET_DPAPI_PREFIX + base64.b64encode(bytes(blob)).decode("ascii")


def _dpapi_unprotect(stored: str) -> str:
    import win32crypt  # type: ignore[import-not-found]

    blob = base64.b64decode(stored[len(LOCAL_SECRET_DPAPI_PREFIX) :])
    return bytes(win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]).decode("utf-8")


def _env_flag(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _insecure_plaintext_allowed() -> bool:
    return (
        _env_flag(ALLOW_INSECURE_LOCAL_SECRETS_ENV)
        or _env_flag("LENGRVIS_TEST")
        or bool(str(os.getenv("PYTEST_CURRENT_TEST") or "").strip())
    )


def _keyring_account(path: Path) -> str:
    try:
        normalized = str(path.expanduser().resolve(strict=False))
    except OSError:
        normalized = str(path)
    return f"local-secret:{sha256(normalized.encode('utf-8')).hexdigest()}"


def _keyring_store(path: Path, value: str) -> str:
    try:
        import keyring  # type: ignore[import-not-found]

        account = _keyring_account(path)
        keyring.set_password(_KEYRING_SERVICE, account, value)
    except Exception as exc:  # noqa: BLE001 - backends vary by platform.
        raise RuntimeError("System keyring is unavailable for local secret storage.") from exc
    return LOCAL_SECRET_KEYRING_PREFIX + account


def _keyring_read(stored: str) -> str:
    account = stored[len(LOCAL_SECRET_KEYRING_PREFIX) :].strip()
    if not account:
        raise RuntimeError("Local secret keyring reference is empty.")
    try:
        import keyring  # type: ignore[import-not-found]

        value = keyring.get_password(_KEYRING_SERVICE, account)
    except Exception as exc:  # noqa: BLE001 - backends vary by platform.
        raise RuntimeError("System keyring is unavailable for local secret storage.") from exc
    if not value:
        raise RuntimeError("Local secret is missing from the system keyring.")
    return str(value)


def _secure_storage_available() -> bool:
    return dpapi_available() or keyring_available()


def _stored_secret_value(path: Path, value: str) -> str:
    if dpapi_available():
        return _dpapi_protect(value)
    if keyring_available():
        return _keyring_store(path, value)
    if _insecure_plaintext_allowed():
        logger.warning(
            "storing plaintext local secret at %s because secure local secret storage is unavailable "
            "and %s/test mode is enabled",
            path,
            ALLOW_INSECURE_LOCAL_SECRETS_ENV,
        )
        return value
    raise RuntimeError(
        "Secure local secret storage is unavailable. Configure a system keyring or set an explicit secret "
        f"environment variable; {ALLOW_INSECURE_LOCAL_SECRETS_ENV}=1 is only for local development/tests."
    )


def _write_secret_file(path: Path, value: str) -> None:
    stored = _stored_secret_value(path, value)
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
    if stored.startswith(LOCAL_SECRET_KEYRING_PREFIX):
        return _keyring_read(stored)
    if _insecure_plaintext_allowed():
        return stored
    raise RuntimeError(
        f"Refusing to read plaintext local secret at {path}; secure storage is required outside tests/dev."
    )
    return stored


def load_or_create_local_secret(path: Path, *, unavailable_message: str) -> str:
    """Load the secret at ``path``, creating (and encrypting) it when missing.

    Plaintext files from older versions are migrated to secure local storage
    when available. Raises ``RuntimeError`` with
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
            if stored.startswith(LOCAL_SECRET_KEYRING_PREFIX):
                try:
                    return _keyring_read(stored)
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(unavailable_message) from exc
            if stored:
                if _secure_storage_available():
                    try:
                        _write_secret_file(path, stored)
                        logger.info("migrated plaintext local secret at %s to secure local storage", path)
                    except (OSError, RuntimeError) as exc:
                        logger.debug("could not migrate local secret at %s: %s", path, exc)
                        if not _insecure_plaintext_allowed():
                            raise RuntimeError(unavailable_message) from exc
                elif not _insecure_plaintext_allowed():
                    raise RuntimeError(unavailable_message)
                return stored
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        _write_secret_file(path, value)
        return value
    except RuntimeError:
        raise
    except OSError as exc:
        raise RuntimeError(unavailable_message) from exc
