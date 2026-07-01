"""Regression tests for ONNX model path hardening (P1-18)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.acceleration import onnx_sessions
from app.acceleration.onnx_sessions import resolve_onnx_model_path


def test_resolve_onnx_model_path_rejects_symlink_escape(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.onnx").write_bytes(b"onnx")

    link = models_dir / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")

    assert resolve_onnx_model_path(link / "secret.onnx") is None


def test_resolve_onnx_model_path_accepts_in_tree_model(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    model = bundle / "model.onnx"
    model.write_bytes(b"onnx")

    assert resolve_onnx_model_path(bundle) == model.resolve()


def test_resolve_onnx_model_path_verifies_optional_manifest_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_dir = tmp_path / "models"
    bundle = models_dir / "clip"
    bundle.mkdir(parents=True)
    model = bundle / "vision_model.onnx"
    payload = b"pinned-onnx"
    model.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    manifest = {
        "models_root": str(models_dir),
        "models": [{"id": "clip", "path": "clip", "model_sha256": digest}],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setenv("LENGRVIS_MODEL_MANIFEST", str(manifest_path))
    monkeypatch.setenv("LENGRVIS_ONNX_MODELS_DIR", str(models_dir))

    assert resolve_onnx_model_path(bundle) == model.resolve()

    model.write_bytes(b"tampered")
    assert resolve_onnx_model_path(bundle) is None


def test_onnx_containment_roots_settings_lookup_failures_do_not_block_resolution(monkeypatch, tmp_path: Path) -> None:
    import app.config as config_module

    def buggy_settings_error():
        raise RuntimeError("settings loader bug")

    monkeypatch.setattr(config_module, "get_base_settings", buggy_settings_error)
    assert onnx_sessions._onnx_containment_roots(tmp_path)
