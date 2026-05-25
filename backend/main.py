from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, create_app

__all__ = ["app", "create_app"]


def main() -> int:
    host = os.environ.get("MAVRIS_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("MAVRIS_BACKEND_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("MAVRIS_BACKEND_LOG_LEVEL", "info"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
