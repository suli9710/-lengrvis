from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_agentic_threat_model.py"
MODEL_PATH = REPO_ROOT / "docs" / "compliance" / "agentic-threat-model.md"
MAP_PATH = REPO_ROOT / "docs" / "compliance" / "agentic-control-map.json"

spec = importlib.util.spec_from_file_location("check_agentic_threat_model", SCRIPT_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_agentic_threat_model_and_control_map_are_complete() -> None:
    model_text = MODEL_PATH.read_text(encoding="utf-8")
    control_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))

    assert mod.validate(model_text, control_map, root=REPO_ROOT) == []


def test_agentic_control_map_rejects_missing_owasp_row() -> None:
    model_text = MODEL_PATH.read_text(encoding="utf-8")
    control_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    control_map["controls"] = [item for item in control_map["controls"] if item["id"] != "ASI06"]

    errors = mod.validate(model_text, control_map, root=REPO_ROOT)

    assert any("ASI06" in error for error in errors)


def test_agentic_control_map_rejects_version_drift() -> None:
    model_text = MODEL_PATH.read_text(encoding="utf-8")
    control_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    control_map["threat_model_version"] = "TM-2026-07-12-v2"

    errors = mod.validate(model_text, control_map, root=REPO_ROOT)

    assert any("version must match" in error for error in errors)


def test_agentic_threat_model_is_wired_into_release_gates() -> None:
    assert mod.validate_repo_wiring(REPO_ROOT) == []
