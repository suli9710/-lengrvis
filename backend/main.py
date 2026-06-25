from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.guardian import app, create_guardian_app  # noqa: E402

create_app = create_guardian_app

__all__ = ["app", "create_app", "full_app", "create_full_app"]


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value:
        return value
    return default


def _env_flag(name: str) -> bool:
    return _env(name).strip().lower() in {"1", "true", "yes", "on"}


def create_full_app() -> Any:
    from app.main import create_app as _create_full_app

    return _create_full_app()


async def full_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    from app.main import app as _full_app

    await _full_app(scope, receive, send)


def main() -> int:
    from app.security.lan import require_secure_non_loopback_bind

    host = _env("LENGRVIS_BACKEND_HOST", "127.0.0.1")
    port = int(_env("LENGRVIS_BACKEND_PORT", "8000"))
    cert_file = _env("LENGRVIS_LAN_TLS_CERT_FILE")
    key_file = _env("LENGRVIS_LAN_TLS_KEY_FILE")
    tls_enabled = _env_flag("LENGRVIS_LAN_TLS_ENABLED")
    require_secure_non_loopback_bind(host, tls_enabled=tls_enabled, cert_file=cert_file, key_file=key_file)
    target_app = create_full_app() if _env("LENGRVIS_FULL_BACKEND") == "1" else app
    uvicorn_options = {
        "host": host,
        "port": port,
        "log_level": _env("LENGRVIS_BACKEND_LOG_LEVEL", "info"),
    }
    if tls_enabled and cert_file and key_file:
        uvicorn_options["ssl_certfile"] = cert_file
        uvicorn_options["ssl_keyfile"] = key_file
    uvicorn.run(target_app, **uvicorn_options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
