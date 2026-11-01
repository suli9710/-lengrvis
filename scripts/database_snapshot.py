"""Create encrypted backups or verify recovery without replacing a live DB."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--verify",
        metavar="SNAPSHOT_NAME",
        help="Recover in memory and verify; do not create a backup.",
    )
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    os.environ["LENGRVIS_DATA_DIR"] = str(data_dir)
    from app.core.db_backups import create_database_snapshot, verify_database_snapshot
    from app.core.sqlite_runtime import require_safe_sqlite
    from app.tools.filesystem_safety import ensure_mutation_path_safe

    require_safe_sqlite()
    if args.verify:
        name = args.verify
    else:
        path = data_dir / "lengrvis.db"
        ensure_mutation_path_safe(path, [str(data_dir)], include_self=True)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            name = create_database_snapshot(conn, data_dir).name
    print(json.dumps(verify_database_snapshot(data_dir, name), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
