from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote


PYTHON_REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*==\s*(?P<version>[^\s;]+)"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a repository CycloneDX SBOM.")
    parser.add_argument("--root", default="", help="Repository root. Defaults to the parent of scripts/.")
    parser.add_argument("--output", default="", help="Output path for the CycloneDX JSON SBOM.")
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve() if args.output else root / ".tmp" / "sbom" / "lengrvis-sbom.cdx.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    commit_sha = git_value(root, ["rev-parse", "HEAD"]) or os.environ.get("GITHUB_SHA", "unknown")
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    components: dict[str, dict[str, Any]] = {}

    add_python_requirements(root / "backend" / "requirements-lock.txt", components)
    add_npm_lock(root / "desktop" / "package-lock.json", "desktop", components)
    add_npm_lock(root / "mobile" / "package-lock.json", "mobile", components)

    component_list = sorted(
        components.values(),
        key=lambda item: (str(item.get("purl") or ""), str(item.get("name") or "")),
    )

    bom = {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "lengrvis-repository-sbom-generator",
                        "version": "1",
                    }
                ]
            },
            "component": {
                "type": "application",
                "name": "lengrvis",
                "version": read_root_version(root),
                "bom-ref": "pkg:generic/lengrvis",
            },
            "properties": [
                {"name": "lengrvis:commit_sha", "value": commit_sha},
                {"name": "lengrvis:source_files", "value": "backend/requirements-lock.txt;desktop/package-lock.json;mobile/package-lock.json"},
            ],
        },
        "components": component_list,
    }

    output.write_text(json.dumps(bom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    by_ecosystem: dict[str, int] = {}
    for component in component_list:
        for prop in component.get("properties", []):
            if prop.get("name") == "lengrvis:ecosystem":
                by_ecosystem[prop.get("value", "unknown")] = by_ecosystem.get(prop.get("value", "unknown"), 0) + 1

    print(f"SBOM generated: {output}")
    print(f"Components: {len(component_list)}")
    for ecosystem in sorted(by_ecosystem):
        print(f" - {ecosystem}: {by_ecosystem[ecosystem]}")
    return 0


def add_python_requirements(path: Path, components: dict[str, dict[str, Any]]) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing Python lock file: {path}")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = PYTHON_REQUIREMENT_RE.match(line)
        if not match:
            continue
        name = normalize_python_name(match.group("name"))
        version = match.group("version")
        purl = f"pkg:pypi/{quote(name, safe='')}@{quote(version, safe='')}"
        upsert_component(
            components,
            purl,
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "bom-ref": purl,
                "properties": [
                    {"name": "lengrvis:ecosystem", "value": "python"},
                    {"name": "lengrvis:source", "value": "backend/requirements-lock.txt"},
                ],
            },
        )


def add_npm_lock(path: Path, project: str, components: dict[str, dict[str, Any]]) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing npm lock file: {path}")

    lock = json.loads(path.read_text(encoding="utf-8"))
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise ValueError(f"{path} is missing packages")

    for package_path, entry in packages.items():
        if not package_path or not isinstance(entry, dict) or entry.get("link"):
            continue
        version = entry.get("version")
        if not isinstance(version, str) or not version:
            continue
        name = entry.get("name") if isinstance(entry.get("name"), str) else npm_name_from_lock_path(package_path)
        if not name:
            continue
        encoded_name = quote(name, safe="/")
        encoded_version = quote(version, safe="")
        purl = f"pkg:npm/{encoded_name}@{encoded_version}"
        scope = "optional" if entry.get("optional") else "required"
        upsert_component(
            components,
            purl,
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "bom-ref": purl,
                "scope": scope,
                "properties": [
                    {"name": "lengrvis:ecosystem", "value": "npm"},
                    {"name": "lengrvis:source", "value": f"{project}/package-lock.json"},
                    {"name": "lengrvis:npm_project", "value": project},
                    {"name": "lengrvis:npm_dev_dependency", "value": "true" if entry.get("dev") else "false"},
                ],
            },
        )


def upsert_component(components: dict[str, dict[str, Any]], key: str, component: dict[str, Any]) -> None:
    existing = components.get(key)
    if existing is None:
        components[key] = component
        return

    existing_props = {(prop.get("name"), prop.get("value")) for prop in existing.get("properties", [])}
    for prop in component.get("properties", []):
        prop_key = (prop.get("name"), prop.get("value"))
        if prop_key not in existing_props:
            existing.setdefault("properties", []).append(prop)
            existing_props.add(prop_key)
    if existing.get("scope") == "optional" and component.get("scope") == "required":
        existing["scope"] = "required"


def npm_name_from_lock_path(package_path: str) -> str:
    parts = list(PurePosixPath(package_path).parts)
    try:
        index = len(parts) - 1 - list(reversed(parts)).index("node_modules")
    except ValueError:
        return ""
    remaining = parts[index + 1 :]
    if not remaining:
        return ""
    if remaining[0].startswith("@") and len(remaining) >= 2:
        return f"{remaining[0]}/{remaining[1]}"
    return remaining[0]


def normalize_python_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_root_version(root: Path) -> str:
    try:
        package_json = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "0.0.0"
    version = package_json.get("version")
    return version if isinstance(version, str) and version else "0.0.0"


def git_value(root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
