from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

MAC_TARGET_ARCHES = {"x86_64", "arm64", "universal2"}

# Optional runtime capabilities bundled by PyInstaller only when the build
# environment has them installed. The emitted manifest lets the packaging gate
# detect build machines that silently produce a backend.exe with fewer
# capabilities than the release expects.
OPTIONAL_CAPABILITY_MODULES: dict[str, str] = {
    "docling": "docling",
    "unstructured": "unstructured",
    "paddleocr": "paddleocr",
    "pytesseract": "pytesseract",
    "playwright": "playwright",
    "pywhispercpp": "pywhispercpp",
}

CAPABILITY_MANIFEST_SCHEMA = "lengrvis-backend-capabilities/v1"


def write_capability_manifest(dist_dir: Path) -> Path:
    capabilities = {
        name: importlib.util.find_spec(module) is not None for name, module in OPTIONAL_CAPABILITY_MODULES.items()
    }
    manifest = {
        "schema": CAPABILITY_MANIFEST_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": sys.platform,
        "capabilities": capabilities,
    }
    dist_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dist_dir / "backend-capabilities.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Backend capability manifest written to {manifest_path}")
    return manifest_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Lengrvis backend as a PyInstaller binary.")
    parser.add_argument(
        "--target-arch",
        choices=sorted(MAC_TARGET_ARCHES),
        help="macOS-only PyInstaller target architecture: x86_64, arm64, or universal2.",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        default=os.environ.get("LENGRVIS_BACKEND_ONEDIR", "").lower() in {"1", "true", "yes", "on"},
        help=(
            "Build a --onedir bundle into dist/backend-onedir/ instead of the default "
            "--onefile dist/backend(.exe). Onedir skips the per-launch self-extraction "
            "of onefile, which cuts backend cold-start time noticeably. The packaging "
            "pipeline still consumes the onefile artifact; onedir is opt-in until the "
            "Electron resources integration is validated end to end."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.target_arch and sys.platform != "darwin":
        print("--target-arch is only supported when building on macOS.", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    backend_dir = root / "backend"
    source_dir = Path(tempfile.mkdtemp(prefix="lengrvis-backend-src-"))
    shutil.copy2(backend_dir / "main.py", source_dir / "main.py")
    shutil.copytree(
        backend_dir / "app",
        source_dir / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_dir)
    # Onedir bundles go to a separate dist dir so the default onefile artifact
    # (dist/backend.exe, consumed by every packaging script) is never clobbered.
    dist_dir = root / ("dist/backend-onedir" if args.onedir else "dist")
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
        "--onedir" if args.onedir else "--onefile",
        "--name",
        "backend",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(root / "build" / "backend"),
        "--specpath",
        str(root / "build" / "backend"),
    ]
    if args.target_arch:
        command.extend(["--target-architecture", args.target_arch])
    command.append("main.py")

    try:
        exit_code = subprocess.call(command, cwd=source_dir, env=env)  # noqa: S603
        if exit_code == 0:
            write_capability_manifest(dist_dir if args.onedir else root / "dist")
        return exit_code
    finally:
        shutil.rmtree(source_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
