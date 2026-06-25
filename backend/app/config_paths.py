from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "backend"
DEFAULT_DATA_DIR = PROJECT_ROOT / ".lengrvis_data"
CONFIG_PARENT_SEARCH_DEPTH = 5
DPAPI_PREFIX = "dpapi:"
ENV_PREFIX = "LENGRVIS"
MOBILE_JWT_SECRET_ENV_KEYS = ("LENGRVIS_JWT_SECRET",)
MOBILE_JWT_SECRET_FILE = "mobile_jwt.secret"  # noqa: S105
