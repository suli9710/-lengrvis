from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Any

from app.config_paths import (
    CONFIG_PARENT_SEARCH_DEPTH,
    DPAPI_PREFIX,
    MOBILE_JWT_SECRET_FILE,
    PROJECT_ROOT,
)

try:
    import yaml
except Exception:  # noqa: BLE001  # pragma: no cover - optional dependency guard
    yaml = None


def configured(raw: Any) -> bool:
    return raw is not None and not (isinstance(raw, str) and raw == "")


def env_aliases(env_key: str) -> tuple[str, ...]:
    key = str(env_key or "").strip()
    if not key:
        return ()
    return (key,)


def env_value(source: dict[str, str] | os._Environ[str], env_key: str) -> str | None:
    for alias in env_aliases(env_key):
        raw = source.get(alias)
        if configured(raw):
            return raw
    return None


def get_env(env_key: str, default: str | None = None) -> str | None:
    raw = env_value(os.environ, env_key)
    return raw if raw is not None else default


def env_raw(env_key: str) -> str | None:
    """Raw environment read that preserves "set but empty" values."""
    for alias in env_aliases(env_key):
        if alias in os.environ:
            return os.environ[alias]
    return None


def env_flag(env_key: str, default: bool = False) -> bool:
    """Shared truthy-flag parsing for boolean environment switches."""
    raw = get_env(env_key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or yaml is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def decrypt_windows_dpapi(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    payload = text[len(DPAPI_PREFIX) :] if text.startswith(DPAPI_PREFIX) else text
    if os.name != "nt":
        raise RuntimeError("Encrypted API keys require Windows DPAPI on this platform.")
    try:
        import win32crypt  # type: ignore[import-not-found]

        blob = base64.b64decode(payload)
        return str(win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - callers need a clear config failure.
        raise RuntimeError("Failed to decrypt LENGRVIS_API_KEY_ENCRYPTED with Windows DPAPI.") from exc


def resolve_api_key(raw_plain: Any, raw_encrypted: Any) -> str:
    plain = str(raw_plain or "").strip()
    if plain:
        return plain
    encrypted = str(raw_encrypted or "").strip()
    if not encrypted:
        return ""
    return decrypt_windows_dpapi(encrypted)


def resolve_mobile_jwt_secret(raw_secret: Any, data_dir: str | Path) -> str:
    configured_secret = str(raw_secret or "").strip()
    if configured_secret:
        return configured_secret
    return local_mobile_jwt_secret(Path(data_dir))


def local_mobile_jwt_secret(data_dir: Path) -> str:
    from app.security.local_secret import load_or_create_local_secret

    return load_or_create_local_secret(
        data_dir / MOBILE_JWT_SECRET_FILE,
        unavailable_message="Mobile JWT secret is unavailable.",
    )


def candidate_config_dirs() -> list[Path]:
    roots: list[Path] = []
    for value in (
        get_env("LENGRVIS_CONFIG_DIR"),
        os.getcwd(),
        PROJECT_ROOT,
    ):
        if value:
            roots.append(Path(value))

    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)

    seen: set[str] = set()
    dirs: list[Path] = []
    for root in roots:
        try:
            current = root.resolve()
        except OSError:
            current = root
        for index, candidate in enumerate([current, *current.parents]):
            if index > CONFIG_PARENT_SEARCH_DEPTH:
                break
            key = str(candidate).lower()
            if key not in seen:
                seen.add(key)
                dirs.append(candidate)
    return dirs


def find_config_file(file_name: str, explicit_env_key: str) -> Path | None:
    explicit = get_env(explicit_env_key)
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    for directory in candidate_config_dirs():
        path = directory / file_name
        if path.exists():
            return path
    return None


def external_data_dir(config_file: Path | None, env_file: Path | None) -> Path:
    anchor = env_file or config_file
    if anchor:
        return preferred_data_dir(anchor.parent)
    return preferred_data_dir(PROJECT_ROOT)


def preferred_data_dir(parent: Path) -> Path:
    return parent / ".lengrvis_data"
