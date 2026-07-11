from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.llm import prompts
from app.main import create_app
from app.mcp.client import MCPClient, MCPServerConfig
from app.mcp.registry import MCPRegistry
from app.policy.permissions import PermissionPolicy, evaluate_permission_policy
from app.policy.risk import RiskLevel
from app.security.capability_manifest import (
    CapabilityRevocationConfigError,
    CapabilityRevokedError,
    _reset_observed_for_tests,
    assert_capability_allowed,
    build_capability_manifest,
    canonical_content_hash,
    canonical_json_bytes,
    mcp_server_capability_payload,
    permission_policy_capability_payload,
    prompt_capability_payload,
    tool_capability_payload,
)
from app.skills.loader import load_skill_package, register_skills
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def isolate_capability_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LENGRVIS_CAPABILITY_REVOCATIONS", raising=False)
    monkeypatch.delenv("LENGRVIS_CAPABILITY_REVOCATION_FILE", raising=False)
    _reset_observed_for_tests()
    prompts.clear_prompt_cache()
    yield
    prompts.clear_prompt_cache()
    _reset_observed_for_tests()


def test_canonical_hash_is_stable_and_mcp_secrets_are_excluded() -> None:
    first = mcp_server_capability_payload(
        {
            "name": "finance",
            "url": "https://alice:password@example.com/mcp?access_token=secret-one",
            "transport": "http",
            "auth": {"token": "secret-one"},
            "allowed_tools": ["lookup", "submit"],
        }
    )
    second = mcp_server_capability_payload(
        {
            "allowed_tools": ["lookup", "submit"],
            "auth": {"token": "secret-two"},
            "transport": "http",
            "url": "https://bob:other@example.com/mcp?access_token=secret-two",
            "name": "finance",
        }
    )

    assert first["endpoint"] == "https://example.com/mcp"
    assert canonical_content_hash(first) == canonical_content_hash(second)
    serialized = json.dumps(first, sort_keys=True)
    assert "password" not in serialized
    assert "secret-one" not in serialized
    assert "secret-two" not in serialized


def test_canonical_tool_hash_preserves_sensitive_field_schema_but_not_secret_header_values() -> None:
    string_password = _test_tool()
    string_password.input_schema = {
        "type": "object",
        "properties": {"password": {"type": "string"}},
    }
    integer_password = _test_tool()
    integer_password.input_schema = {
        "type": "object",
        "properties": {"password": {"type": "integer"}},
    }

    assert canonical_content_hash(tool_capability_payload(string_password)) != canonical_content_hash(
        tool_capability_payload(integer_password)
    )
    sanitized = canonical_json_bytes(
        {"execution": {"headers": {"Authorization": "Bearer top-secret"}}, "input_schema": string_password.input_schema}
    ).decode("utf-8")
    assert '"password"' in sanitized
    assert "top-secret" not in sanitized


def test_tool_revocation_by_id_blocks_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CAPABILITY_REVOCATIONS", "tool:test.echo")
    registry = ToolRegistry()

    registry.register(_test_tool())

    with pytest.raises(KeyError, match="not registered"):
        registry.get("test.echo")


def test_capability_block_audit_contains_only_identifier_and_content_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "app.core.audit.record",
        lambda event_type, actor, payload: events.append((event_type, actor, payload)),
    )
    monkeypatch.setenv("LENGRVIS_CAPABILITY_REVOCATIONS", "tool:test.echo")

    with pytest.raises(CapabilityRevokedError):
        assert_capability_allowed("tool", "test.echo", payload={"description": "private description"})

    assert events[0][0] == "security.capability_blocked"
    payload = events[0][2]
    assert set(payload) == {
        "kind",
        "capability_id_hash",
        "content_hash",
        "reason",
        "revocation_sources",
    }
    serialized = json.dumps(payload, sort_keys=True)
    assert "test.echo" not in serialized
    assert "private description" not in serialized


def test_tool_revocation_by_hash_blocks_already_registered_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    tool = _test_tool(execute=lambda args, _context: calls.append(args) or {"ok": True})
    registry = ToolRegistry()
    registry.register(tool)
    registered = registry.get(tool.name)
    digest = canonical_content_hash(tool_capability_payload(registered))
    monkeypatch.setenv(
        "LENGRVIS_CAPABILITY_REVOCATIONS",
        json.dumps({"revocations": [{"content_hash": digest}]}),
    )

    with pytest.raises(CapabilityRevokedError):
        registered.execute({"value": 1}, {})

    assert calls == []
    assert registry.list_for_planning() == []


def test_prompt_revocation_by_hash_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "guarded.md").write_text("Never submit without approval.", encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPT_DIR", prompt_dir)
    digest = canonical_content_hash(prompt_capability_payload("Never submit without approval."))
    monkeypatch.setenv("LENGRVIS_CAPABILITY_REVOCATIONS", digest)

    with pytest.raises(CapabilityRevokedError):
        prompts.load_prompt("guarded.md")


def test_invalid_explicit_revocation_file_disables_protected_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    revocations = tmp_path / "revocations.json"
    revocations.write_text("{not-json", encoding="utf-8")
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "guarded.md").write_text("Protected", encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPT_DIR", prompt_dir)
    monkeypatch.setenv("LENGRVIS_CAPABILITY_REVOCATION_FILE", str(revocations))

    with pytest.raises(CapabilityRevocationConfigError):
        prompts.load_prompt("guarded.md")


def test_revoked_permission_policy_fails_closed_at_evaluation_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = PermissionPolicy(id="operations", rules=[])
    digest = canonical_content_hash(permission_policy_capability_payload(policy))
    monkeypatch.setenv(
        "LENGRVIS_CAPABILITY_REVOCATIONS",
        json.dumps({"revocations": [{"kind": "permission_policy", "id": "operations"}]}),
    )

    with pytest.raises(CapabilityRevokedError):
        evaluate_permission_policy(policy, tool_name="file.read", args={})

    monkeypatch.setenv("LENGRVIS_CAPABILITY_REVOCATIONS", digest)
    with pytest.raises(CapabilityRevokedError):
        evaluate_permission_policy(policy, tool_name="file.read", args={})


def test_skill_origin_version_and_skill_level_revocation_block_tool_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skill_root = _write_skill(tmp_path)
    package = load_skill_package(skill_root)
    tool = package.tool_definitions[0]

    assert tool.origin == "skill:manifest-demo"
    assert tool.tool_version == "2.3.0"
    assert package.manifest_hash.startswith("sha256:")

    monkeypatch.setenv("LENGRVIS_CAPABILITY_REVOCATIONS", "skill:manifest-demo")
    registry = ToolRegistry()
    register_skills(registry, skill_directories=[tmp_path])

    with pytest.raises(KeyError, match="not registered"):
        registry.get("skill.manifest_demo.read")


def test_skill_level_revocation_blocks_an_already_registered_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path)
    registry = ToolRegistry()
    register_skills(registry, skill_directories=[tmp_path])
    registered = registry.get("skill.manifest_demo.read")

    monkeypatch.setenv("LENGRVIS_CAPABILITY_REVOCATIONS", "skill:manifest-demo")

    with pytest.raises(CapabilityRevokedError):
        registered.execute({}, {})


def test_mcp_server_revocation_blocks_load_and_existing_client(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(
        provider_name="mock",
        mcp_servers=[{"name": "revoked-demo", "url": "https://example.com/mcp", "enabled": True}],
    )
    client = MCPClient(MCPServerConfig(name="revoked-demo", url="https://example.com/mcp"))
    monkeypatch.setenv("LENGRVIS_CAPABILITY_REVOCATIONS", "mcp_server:revoked-demo")
    registry = MCPRegistry()

    registry.load_from_settings(settings)
    result = asyncio.run(client.call_tool("echo", {}))

    assert registry.clients == {}
    assert result["ok"] is False
    assert "revoked" in result["error"].casefold()


def test_mcp_tools_include_server_origin_and_schema_bound_version(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = MCPRegistry()
    registry.clients["demo"] = MCPClient(MCPServerConfig(name="demo", url="https://example.com/mcp"))

    async def fake_list_all_tools() -> list[dict]:
        return [
            {
                "server": "demo",
                "name": "lookup",
                "description": "Look up a record.",
                "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
            }
        ]

    monkeypatch.setattr(registry, "list_all_tools", fake_list_all_tools)

    definition = asyncio.run(registry.adapt_to_tool_definitions())[0]

    assert definition.origin == "mcp:demo"
    assert definition.tool_version.startswith("sha256:")


def test_read_only_manifest_api_exposes_hashes_without_capability_contents_or_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LENGRVIS_MCP_SERVERS",
        json.dumps(
            [
                {
                    "name": "token=top-secret",
                    "url": "https://user:password@example.com/mcp?token=top-secret",
                    "auth": {"token": "top-secret"},
                }
            ]
        ),
    )
    app = create_app()
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/security/capability-manifest")

    assert route.methods == {"GET"}
    response = TestClient(app).get("/api/security/capability-manifest")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert payload["format"] == "lengrvis.capability-manifest/v1"
    assert payload["schema_version"] == 1
    assert payload["manifest_hash"].startswith("sha256:")
    assert "top-secret" not in serialized
    assert "password" not in serialized
    assert "input_schema" not in serialized
    assert "Never submit" not in serialized


def test_manifest_hash_is_versioned_and_deterministic_for_same_capabilities() -> None:
    settings = AppSettings(provider_name="mock", mcp_servers=[])
    tool = _test_tool()

    first = build_capability_manifest(settings=settings, tools=[tool])
    second = build_capability_manifest(settings=settings, tools=[tool])

    assert first["manifest_id"] == second["manifest_id"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["generated_at"] != ""


def test_manifest_covers_prompts_tools_policy_skills_and_mcp_config(tmp_path: Path) -> None:
    _write_skill(tmp_path)
    load_skill_package(tmp_path / "manifest_demo")
    settings = AppSettings(
        provider_name="mock",
        data_dir=str(tmp_path / "data"),
        skill_directories=[str(tmp_path)],
        mcp_servers=[{"name": "manifest-mcp", "url": "https://example.com/mcp"}],
    )

    manifest = build_capability_manifest(settings=settings, tools=[_test_tool()])
    kinds = {entry["kind"] for entry in manifest["entries"]}
    skill_tool = next(entry for entry in manifest["entries"] if entry["id"] == "skill.manifest_demo.read")

    assert {"prompt", "tool", "permission_policy", "skill", "mcp_server"}.issubset(kinds)
    assert skill_tool["origin"] == "skill:manifest-demo"
    assert skill_tool["version"] == "2.3.0"


def _test_tool(*, execute=None) -> ToolDefinition:
    return ToolDefinition(
        name="test.echo",
        description="Return a test value.",
        input_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
        output_schema={"type": "object"},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="TestAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=execute or (lambda args, _context: {"ok": True, "value": args.get("value")}),
        read_only=True,
        concurrency_safe=True,
        effects=["read"],
        resource_kinds=["runtime"],
        trust_tier="builtin",
        origin="builtin",
        tool_version="1",
    )


def _write_skill(root: Path) -> Path:
    skill_root = root / "manifest_demo"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """
name: manifest-demo
version: "2.3.0"
agent_owner: FileAgent
risk: R0_READ_ONLY
permissions:
  - filesystem.read
tools:
  - name: skill.manifest_demo.read
    description: Read a local manifest demo value.
    input_schema:
      type: object
    output_schema:
      type: object
    execution:
      type: python
      entry: handler.py
""".strip(),
        encoding="utf-8",
    )
    (skill_root / "handler.py").write_text(
        "import json\nprint(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    return skill_root
