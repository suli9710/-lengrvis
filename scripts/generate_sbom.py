from __future__ import annotations

import argparse
import base64
import binascii
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
PYTHON_HASH_RE = re.compile(r"--hash\s*=\s*sha256:(?P<hash>[a-fA-F0-9]{64})")
PYTHON_LOCK_SOURCES = (
    "backend/requirements-lock.txt",
    "backend/requirements-build-lock.txt",
    "scripts/acceleration-requirements-lock.txt",
)
NPM_LOCK_SOURCES = (
    ("desktop/package-lock.json", "desktop"),
    ("mobile/package-lock.json", "mobile"),
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

    for source in PYTHON_LOCK_SOURCES:
        add_python_requirements(root / source, source, components)
    for source, project in NPM_LOCK_SOURCES:
        add_npm_lock(root / source, project, components)

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
                {
                    "name": "lengrvis:source_files",
                    "value": ";".join([*PYTHON_LOCK_SOURCES, *(source for source, _project in NPM_LOCK_SOURCES)]),
                },
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


def add_python_requirements(path: Path, source_label: str, components: dict[str, dict[str, Any]]) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing Python lock file: {path}")

    python_components: dict[str, dict[str, Any]] = {}
    current_purl = ""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        hash_match = PYTHON_HASH_RE.search(line)
        if hash_match and current_purl:
            python_components[current_purl].setdefault("hashes", []).append(
                {"alg": "SHA-256", "content": hash_match.group("hash").lower()}
            )
            continue

        if line.startswith("-"):
            continue

        match = PYTHON_REQUIREMENT_RE.match(line)
        if not match:
            continue
        name = normalize_python_name(match.group("name"))
        version = match.group("version")
        purl = f"pkg:pypi/{quote(name, safe='')}@{quote(version, safe='')}"
        current_purl = purl
        python_components[purl] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "bom-ref": purl,
            "properties": [
                {"name": "lengrvis:ecosystem", "value": "python"},
                {"name": "lengrvis:source", "value": source_label},
            ],
        }

    for purl, component in python_components.items():
        if "hashes" in component:
            component["properties"].append(
                {"name": "lengrvis:pypi_sha256_hash_count", "value": str(len(component["hashes"]))}
            )
        upsert_component(components, purl, component)


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
        properties = [
            {"name": "lengrvis:ecosystem", "value": "npm"},
            {"name": "lengrvis:source", "value": f"{project}/package-lock.json"},
            {"name": "lengrvis:npm_project", "value": project},
            {"name": "lengrvis:npm_dev_dependency", "value": "true" if entry.get("dev") else "false"},
        ]
        integrity = entry.get("integrity")
        hashes = npm_integrity_hashes(integrity) if isinstance(integrity, str) else []
        if isinstance(integrity, str) and integrity:
            properties.append({"name": "lengrvis:npm_integrity", "value": integrity})
        component = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "bom-ref": purl,
            "scope": scope,
            "properties": properties,
        }
        if hashes:
            component["hashes"] = hashes
        license_choice = cyclonedx_license_choice(entry.get("license"))
        if license_choice:
            component["licenses"] = [license_choice]
        upsert_component(
            components,
            purl,
            component,
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
    merge_unique_objects(existing, component, "hashes", ("alg", "content"))
    merge_unique_objects(existing, component, "licenses", ("expression", "license"))


def merge_unique_objects(existing: dict[str, Any], component: dict[str, Any], key: str, identity_fields: tuple[str, ...]) -> None:
    incoming = component.get(key)
    if not isinstance(incoming, list):
        return

    existing_items = existing.setdefault(key, [])
    if not isinstance(existing_items, list):
        existing[key] = []
        existing_items = existing[key]

    seen = {json.dumps(object_identity(item, identity_fields), sort_keys=True) for item in existing_items}
    for item in incoming:
        item_key = json.dumps(object_identity(item, identity_fields), sort_keys=True)
        if item_key not in seen:
            existing_items.append(item)
            seen.add(item_key)


def object_identity(item: Any, identity_fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"value": item}
    return {field: item.get(field) for field in identity_fields}


def npm_integrity_hashes(integrity: str) -> list[dict[str, str]]:
    hashes: list[dict[str, str]] = []
    for token in integrity.split():
        if "-" not in token:
            continue
        algorithm, encoded_digest = token.split("-", 1)
        algorithm = algorithm.lower()
        alg_name = {
            "sha1": "SHA-1",
            "sha256": "SHA-256",
            "sha384": "SHA-384",
            "sha512": "SHA-512",
        }.get(algorithm)
        if not alg_name:
            continue
        try:
            digest = base64.b64decode(encoded_digest, validate=True).hex()
        except (ValueError, binascii.Error):
            continue
        hashes.append({"alg": alg_name, "content": digest})
    return hashes


def cyclonedx_license_choice(raw_license: Any) -> dict[str, Any] | None:
    if not isinstance(raw_license, str):
        return None
    license_text = raw_license.strip()
    if not license_text:
        return None
    if re.fullmatch(r"[A-Za-z0-9-.+]+", license_text):
        return {"license": {"id": license_text}}
    return {"expression": license_text}


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
