"""Import alias for backend.app during local development and tests."""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[1] / "backend" / "app")]

