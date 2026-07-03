#!/usr/bin/env python3
"""Verify release-profile Skill/MCP supply-chain controls.

This gate exercises the fail-closed controls without requiring a real release
key or a live third-party MCP server:

- unsigned Skills are rejected when trusted signatures are required;
- a generated Ed25519-signed Skill loads with the trusted test key;
- Skill imports require explicit permission-diff review in release profile and
  write an audit event that records the review;
- MCP release profile requires owner, policy id, and explicit allowed tools;
- MCP adaptation exposes only owner-approved tools.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import AppSettings  # noqa: E402
from app.core import db  # noqa: E402
from app.mcp.client import MCPServerConfig  # noqa: E402
from app.mcp.registry import MCPRegistry  # noqa: E402
from app.services import skill_service  # noqa: E402
from app.skills.loader import canonical_skill_signature_payload, load_skill_package  # noqa: E402
from app.skills.schemas import SkillLoadError  # noqa: E402


@dataclass(slots=True)
class GateCheck:
    name: str
    passed: bool
    detail: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_skill(root: Path, *, name: str, permissions: list[str] | None = None) -> Path:
    skill_root = root / name
    skill_root.mkdir(parents=True)
    (skill_root / "handler.py").write_text("print('{\"ok\": true}')\n", encoding="utf-8")
    manifest: dict[str, Any] = {
        "name": name,
        "version": "1.0",
        "agent_owner": "FileAgent",
        "permissions": permissions or ["filesystem.read"],
        "tools": [
            {
                "name": f"skill.{name.replace('-', '_')}.read",
                "execution": {"type": "python", "entry": "handler.py"},
            }
        ],
    }
    (skill_root / "skill.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return skill_root


def _write_signed_skill(root: Path) -> tuple[Path, str, str]:
    manifest: dict[str, Any] = {
        "name": "signed-release-skill",
        "version": "1.0",
        "agent_owner": "FileAgent",
        "permissions": ["filesystem.read"],
        "tools": [
            {
                "name": "skill.signed_release.read",
                "execution": {"type": "python", "entry": "handler.py"},
            }
        ],
    }
    private_key = Ed25519PrivateKey.generate()
    payload = canonical_skill_signature_payload(manifest)
    public_key = base64.b64encode(
        private_key.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    ).decode("ascii")
    manifest["signature"] = {
        "key_id": "release-profile-test",
        "algorithm": "ed25519",
        "manifest_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "signature": base64.b64encode(private_key.sign(payload)).decode("ascii"),
        "signed_at": "2026-07-03T00:00:00Z",
    }
    skill_root = root / "signed-release-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "handler.py").write_text("print('{\"ok\": true}')\n", encoding="utf-8")
    (skill_root / "skill.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return skill_root, "release-profile-test", public_key


def _check_skill_signature_policy(workspace: Path) -> GateCheck:
    unsigned = _write_skill(workspace / "unsigned", name="unsigned-release-skill")
    try:
        load_skill_package(unsigned, require_trusted_signature=True)
    except SkillLoadError as exc:
        unsigned_blocked = "signed by a trusted key" in str(exc)
    else:
        unsigned_blocked = False

    signed_root, key_id, public_key = _write_signed_skill(workspace / "signed")
    signed = load_skill_package(
        signed_root,
        trusted_public_keys={key_id: public_key},
        require_trusted_signature=True,
    )
    passed = unsigned_blocked and signed.signature_report["status"] == "verified"
    return GateCheck(
        "signed-skill-release-policy",
        passed,
        "unsigned blocked and generated signed Skill verified" if passed else "signature release policy failed",
    )


def _check_permission_diff_review(workspace: Path) -> tuple[GateCheck, dict[str, Any]]:
    data_dir = workspace / "data"
    source = _write_skill(workspace / "source", name="permission-review-skill")
    os.environ["LENGRVIS_DATA_DIR"] = str(data_dir)
    db.init_db()
    original_refresh = skill_service.refresh_runtime_registry

    async def fake_refresh(settings=None):  # noqa: ANN001, ARG001
        return {"ok": True, "tool_count": 0, "skill_count": 1}

    skill_service.refresh_runtime_registry = fake_refresh
    settings = AppSettings(
        provider_name="mock",
        data_dir=str(data_dir),
        allowed_directories=[str(workspace)],
        skill_directories=[str(data_dir / "skills")],
        skill_require_permission_diff_review=True,
    )
    try:
        try:
            asyncio.run(skill_service.import_skill(str(source), settings=settings))
        except skill_service.SkillServiceError as exc:
            blocked = exc.code == "skill_permission_diff_review_required"
        else:
            blocked = False

        result = asyncio.run(
            skill_service.import_skill(str(source), settings=settings, permission_diff_reviewed=True)
        )
    finally:
        skill_service.refresh_runtime_registry = original_refresh

    audit_events = db.fetch_many_by_fields("audit_events", {"event_type": "skills.imported"}, limit=1)
    audit_payload = audit_events[0]["payload"] if audit_events else {}
    passed = (
        blocked
        and result["upgrade_diff"]["added_tools"] == ["skill.permission_review_skill.read"]
        and audit_payload.get("permission_diff_reviewed") is True
        and audit_payload.get("permission_diff_review_required") is True
    )
    detail = "permission diff review blocked until explicitly reviewed and audited" if passed else "diff review failed"
    return GateCheck("skill-permission-diff-review", passed, detail), audit_payload


async def _fake_tools() -> list[dict[str, Any]]:
    return [
        {"name": "echo", "description": "approved", "input_schema": {"type": "object"}},
        {"name": "delete_all", "description": "unapproved", "input_schema": {"type": "object"}},
    ]


def _check_mcp_owner_policy() -> GateCheck:
    missing_policy_blocked = False
    try:
        MCPRegistry().load_from_settings(
            AppSettings(
                provider_name="mock",
                mcp_require_owner_policy=True,
                mcp_servers=[
                    {
                        "name": "unowned",
                        "url": "https://api.example.com/mcp",
                        "enabled": True,
                        "policy_id": "SEC-MCP-1",
                        "allowed_tools": ["echo"],
                    }
                ],
            )
        )
    except ValueError:
        missing_policy_blocked = True

    registry = MCPRegistry()
    config = MCPServerConfig(
        name="approved",
        url="https://api.example.com/mcp",
        owner="security-owner",
        policy_id="SEC-MCP-1",
        allowed_tools=["echo"],
    )

    class FakeClient:
        def __init__(self) -> None:
            self.config = config

        async def list_tools(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:  # noqa: ARG002
            return await _fake_tools()

    registry.clients["approved"] = FakeClient()  # type: ignore[assignment]
    definitions = asyncio.run(registry.adapt_to_tool_definitions())
    passed = missing_policy_blocked and [definition.name for definition in definitions] == ["mcp.approved.echo"]
    return GateCheck(
        "mcp-owner-approved-policy",
        passed,
        "missing owner policy blocked and unapproved tools filtered" if passed else "MCP owner policy failed",
    )


def _write_artifacts(output_root: Path, checks: list[GateCheck], audit_payload: dict[str, Any]) -> tuple[Path, Path]:
    run_root = output_root / f"run-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    run_root.mkdir(parents=True, exist_ok=True)
    passed = all(check.passed for check in checks)
    payload = {
        "generated_by": "scripts/verify_skill_mcp_supply_chain.py",
        "generated_at_utc": _utc_now(),
        "status": "passed" if passed else "failed",
        "claim_controls": {
            "skill_mcp_supply_chain_gate_passed": passed,
            "signed_skill_release_policy_checked": checks[0].passed,
            "permission_diff_review_checked": checks[1].passed,
            "mcp_owner_policy_checked": checks[2].passed,
            "audit_artifact_written": bool(audit_payload),
            "release_signoff": False,
        },
        "checks": [asdict(check) for check in checks],
        "audit_sample": {
            "event_type": "skills.imported" if audit_payload else "",
            "permission_diff_reviewed": audit_payload.get("permission_diff_reviewed"),
            "permission_diff_review_required": audit_payload.get("permission_diff_review_required"),
            "upgrade_diff_keys": sorted((audit_payload.get("upgrade_diff") or {}).keys()),
        },
    }
    json_path = run_root / "skill-mcp-supply-chain-gate.redacted.json"
    md_path = run_root / "skill-mcp-supply-chain-gate.redacted.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Skill/MCP Supply Chain Gate",
        "",
        f"- Status: {payload['status']}",
        f"- Generated at UTC: {payload['generated_at_utc']}",
        "- Release sign-off: false",
        "",
        "## Checks",
        "",
    ]
    for check in checks:
        status = "passed" if check.passed else "failed"
        lines.append(f"- [{status}] {check.name}: {check.detail}")
    lines.extend(
        [
            "",
            "## Audit Sample",
            "",
            f"- Event type: {payload['audit_sample']['event_type'] or 'not recorded'}",
            f"- Permission diff reviewed: {payload['audit_sample']['permission_diff_reviewed']}",
            f"- Permission diff review required: {payload['audit_sample']['permission_diff_review_required']}",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Skill/MCP supply-chain release-profile controls.")
    parser.add_argument("--output-root", default=str(ROOT / ".tmp" / "skill-mcp-supply-chain-gate"))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="lengrvis-skill-mcp-gate-", ignore_cleanup_errors=True) as temp_dir:
        workspace = Path(temp_dir)
        checks: list[GateCheck] = []
        audit_payload: dict[str, Any] = {}
        try:
            checks.append(_check_skill_signature_policy(workspace))
            diff_check, audit_payload = _check_permission_diff_review(workspace)
            checks.append(diff_check)
            checks.append(_check_mcp_owner_policy())
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: gate artifact must explain setup failures.
            checks.append(GateCheck("gate-runtime", False, f"{type(exc).__name__}: {exc}"))

        json_path, md_path = _write_artifacts(Path(args.output_root), checks, audit_payload)

    print(f"Skill/MCP supply-chain gate summary: {json_path}")
    print(f"Skill/MCP supply-chain gate markdown: {md_path}")
    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
