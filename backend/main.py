from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.guardian import app, create_guardian_app

create_app = create_guardian_app

__all__ = ["app", "create_app", "full_app", "create_full_app"]


def create_full_app() -> Any:
    from app.main import create_app as _create_full_app

    return _create_full_app()


async def full_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    from app.main import app as _full_app

    await _full_app(scope, receive, send)


def main() -> int:
    host = os.environ.get("MAVRIS_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("MAVRIS_BACKEND_PORT", "8000"))
    target_app = create_full_app() if os.environ.get("MAVRIS_FULL_BACKEND") == "1" else app
    uvicorn.run(target_app, host=host, port=port, log_level=os.environ.get("MAVRIS_BACKEND_LOG_LEVEL", "info"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
