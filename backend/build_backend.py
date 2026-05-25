from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


MAC_TARGET_ARCHES = {"x86_64", "arm64", "universal2"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Mavris backend as a PyInstaller binary.")
    parser.add_argument(
        "--target-arch",
        choices=sorted(MAC_TARGET_ARCHES),
        help="macOS-only PyInstaller target architecture: x86_64, arm64, or universal2.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.target_arch and sys.platform != "darwin":
        print("--target-arch is only supported when building on macOS.", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    backend_dir = root / "backend"
    source_dir = Path(tempfile.mkdtemp(prefix="marvis-backend-src-"))
    shutil.copy2(backend_dir / "main.py", source_dir / "main.py")
    shutil.copytree(
        backend_dir / "app",
        source_dir / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_dir)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--paths",
        str(source_dir),
        "--collect-submodules",
        "app",
        "--collect-submodules",
        "uvicorn",
        "--collect-data",
        "app",
        "--onefile",
        "--name",
        "backend",
        "--distpath",
        str(root / "dist"),
        "--workpath",
        str(root / "build" / "backend"),
        "--specpath",
        str(root / "build" / "backend"),
    ]
    if args.target_arch:
        command.extend(["--target-architecture", args.target_arch])
    command.append("main.py")

    try:
        return subprocess.call(command, cwd=source_dir, env=env)
    finally:
        shutil.rmtree(source_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
