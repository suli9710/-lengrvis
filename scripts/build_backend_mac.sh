#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Mac backend binary must be built on macOS; PyInstaller cannot cross-build it from this host." >&2
  exit 2
fi

TARGET_ARCH="${LENGRVIS_BACKEND_TARGET_ARCH:-}"
if [[ "${1:-}" == "--target-arch" ]]; then
  TARGET_ARCH="${2:?Missing value for --target-arch}"
  shift 2
elif [[ $# -gt 0 ]]; then
  TARGET_ARCH="$1"
  shift
fi

if [[ $# -gt 0 ]]; then
  echo "Unexpected arguments: $*" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON:-python3}"
BUILD_REQUIREMENTS="backend/requirements-build.txt"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
  PYTHON_BIN="${PYTHON:-python}"
fi

if [[ ! -f "$BUILD_REQUIREMENTS" ]]; then
  echo "Missing pinned backend build dependency file: $BUILD_REQUIREMENTS" >&2
  exit 1
fi

echo "Ensuring pinned backend build dependencies from $BUILD_REQUIREMENTS..."
if ! "$PYTHON_BIN" -m pip install -r "$BUILD_REQUIREMENTS"; then
  echo "Failed to install pinned backend build dependencies. Run: $PYTHON_BIN -m pip install -r $BUILD_REQUIREMENTS" >&2
  exit 1
fi

ARGS=()
if [[ -n "$TARGET_ARCH" ]]; then
  ARGS+=(--target-arch "$TARGET_ARCH")
fi

"$PYTHON_BIN" backend/build_backend.py "${ARGS[@]}"
chmod +x dist/backend

echo "Mac backend binary created at $ROOT/dist/backend"
