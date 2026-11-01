"""Side-effect-free SQLite runtime evidence shared by source and frozen builds."""

from __future__ import annotations

import json
import platform
import re
import sqlite3
import sys
from typing import Any


def wal_reset_fixed(version: str) -> bool:
    """Accept the fixed mainline and the two upstream backport branches.

    https://www.sqlite.org/wal.html#the_wal_reset_bug
    Unknown version strings fail closed; distribution patches need explicit review.
    """
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return False
    parts = tuple(int(part) for part in version.split("."))
    return parts >= (3, 51, 3) or (parts[:2] == (3, 44) and parts[2] >= 6) or (parts[:2] == (3, 50) and parts[2] >= 7)


def runtime_evidence() -> dict[str, Any]:
    with sqlite3.connect(":memory:") as conn:
        source_id = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
        options = sorted(row[0] for row in conn.execute("PRAGMA compile_options"))
    return {
        "schema": "lengrvis-sqlite-runtime/v1",
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_source_id": source_id,
        "compile_options": options,
        "wal_reset_fixed": wal_reset_fixed(sqlite3.sqlite_version),
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def require_safe_sqlite() -> None:
    if not wal_reset_fixed(sqlite3.sqlite_version):
        raise RuntimeError(
            f"SQLite {sqlite3.sqlite_version} lacks the upstream WAL-reset fix. "
            "Use a maintained Python runtime with SQLite >=3.51.3 (or the reviewed "
            "3.44.6/3.50.7 backport branch), then rebuild the backend."
        )


def print_runtime_evidence() -> int:
    evidence = runtime_evidence()
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["wal_reset_fixed"] else 1
