#!/usr/bin/env python3
"""Validate the versioned Agentic threat model and OWASP control map."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "docs" / "compliance" / "agentic-threat-model.md"
DEFAULT_MAP = ROOT / "docs" / "compliance" / "agentic-control-map.json"
EXPECTED_SCHEMA = "lengrvis-agentic-control-map/v1"
EXPECTED_ASI_IDS = {f"ASI{index:02d}" for index in range(1, 11)}
EXPECTED_BOUNDARIES = {f"TB-{index:02d}" for index in range(1, 12)}
VERSION_RE = re.compile(r"^TM-\d{4}-\d{2}-\d{2}-v\d+$")
REQUIRED_WIRING = (
    (ROOT / "package.json", '"security:threat-model"'),
    (ROOT / ".github" / "workflows" / "ci.yml", "npm run security:threat-model"),
    (ROOT / ".github" / "workflows" / "release-candidate.yml", "npm run security:threat-model"),
    (ROOT / ".github" / "workflows" / "release-readiness.yml", "npm run security:threat-model"),
    (ROOT / ".github" / "workflows" / "release-publish.yml", "npm run security:threat-model"),
    (ROOT / "scripts" / "delivery_pipeline.py", '"agentic-threat-model"'),
    (ROOT / "docs" / "release" / "delivery-pipeline.md", "agentic-threat-model"),
    (ROOT / "docs" / "release" / "release-readiness-dashboard.md", "npm run security:threat-model"),
    (ROOT / "docs" / "qa" / "release-gate.md", "security:threat-model"),
)


def _nonempty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def validate(model_text: str, control_map: dict[str, Any], *, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if control_map.get("schema") != EXPECTED_SCHEMA:
        errors.append(f"control map schema must be {EXPECTED_SCHEMA}")

    version = str(control_map.get("threat_model_version") or "").strip()
    if not VERSION_RE.fullmatch(version):
        errors.append("threat_model_version must use TM-YYYY-MM-DD-vN")
    elif f"Version: {version}" not in model_text:
        errors.append("threat model Markdown version must match the control map")

    if "not a penetration-test report" not in model_text:
        errors.append("threat model must state that it is not penetration-test evidence")
    if "npm run security:threat-model" not in model_text:
        errors.append("threat model must name its validation command")

    boundaries = set(_nonempty_strings(control_map.get("trust_boundaries")))
    missing_boundaries = sorted(EXPECTED_BOUNDARIES - boundaries)
    extra_boundaries = sorted(boundaries - EXPECTED_BOUNDARIES)
    if missing_boundaries:
        errors.append(f"control map is missing trust boundaries: {', '.join(missing_boundaries)}")
    if extra_boundaries:
        errors.append(f"control map has unknown trust boundaries: {', '.join(extra_boundaries)}")
    for boundary in sorted(EXPECTED_BOUNDARIES):
        if boundary not in model_text:
            errors.append(f"threat model Markdown is missing {boundary}")

    controls = control_map.get("controls")
    if not isinstance(controls, list):
        errors.append("control map controls must be an array")
        controls = []
    ids = [str(item.get("id") or "") for item in controls if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("control map contains duplicate control ids")
    found_ids = set(ids)
    missing_ids = sorted(EXPECTED_ASI_IDS - found_ids)
    extra_ids = sorted(found_ids - EXPECTED_ASI_IDS)
    if missing_ids:
        errors.append(f"control map is missing OWASP controls: {', '.join(missing_ids)}")
    if extra_ids:
        errors.append(f"control map has unknown OWASP controls: {', '.join(extra_ids)}")

    for item in controls:
        if not isinstance(item, dict):
            errors.append("every control row must be an object")
            continue
        control_id = str(item.get("id") or "<unknown>")
        for field in ("title", "owner", "residual_risk"):
            if not str(item.get(field) or "").strip():
                errors.append(f"{control_id} must define {field}")
        for field in ("threats", "controls", "tests"):
            values = _nonempty_strings(item.get(field))
            if not values:
                errors.append(f"{control_id} must define at least one {field} entry")
            if field == "tests":
                for relative in values:
                    if not (root / relative).is_file():
                        errors.append(f"{control_id} references missing test file: {relative}")
    return errors


def load_inputs(model_path: Path, map_path: Path) -> tuple[str, dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        model_text = model_path.read_text(encoding="utf-8")
    except OSError as exc:
        model_text = ""
        errors.append(f"could not read threat model: {exc}")
    try:
        parsed = json.loads(map_path.read_text(encoding="utf-8"))
        control_map = parsed if isinstance(parsed, dict) else {}
        if not isinstance(parsed, dict):
            errors.append("control map root must be an object")
    except (OSError, json.JSONDecodeError) as exc:
        control_map = {}
        errors.append(f"could not read control map: {exc}")
    return model_text, control_map, errors


def validate_repo_wiring(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for path, phrase in REQUIRED_WIRING:
        candidate = root / path.relative_to(ROOT)
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"could not read threat-model wiring file {candidate}: {exc}")
            continue
        if phrase not in text:
            errors.append(f"{candidate} must include {phrase!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--control-map", type=Path, default=DEFAULT_MAP)
    args = parser.parse_args()

    model_text, control_map, errors = load_inputs(args.model, args.control_map)
    if not errors:
        errors.extend(validate(model_text, control_map, root=ROOT))
        errors.extend(validate_repo_wiring(ROOT))
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
