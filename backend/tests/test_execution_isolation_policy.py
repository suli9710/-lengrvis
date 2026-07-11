from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.config import AppSettings
from app.core.errors import AppError
from app.integrations import lengrvis_code
from app.integrations.lengrvis_code import (
    LengrvisCodeConfig,
    allowed_tools_for_developer,
    run_lengrvis_code,
    validate_allowed_tools,
)
from app.orchestration.engine_router import route_engine
from app.security.execution_isolation import (
    REQUIRED_EXECUTION_ISOLATION_CAPABILITIES,
    ExecutionIsolationAttestation,
    ExecutionIsolationRequiredError,
    arbitrary_execution_allowed,
    assert_release_execution_configuration,
    release_execution_configuration_issues,
    release_profile_active,
)
from app.services import settings_service
from app.skills.sandbox import SkillSandbox
from app.skills.schemas import SkillExecution, SkillExecutionType
from app.tools import developer_tools


def _complete_attestation() -> ExecutionIsolationAttestation:
    return ExecutionIsolationAttestation(
        provider="test-native-host",
        platform="win32",
        capabilities=REQUIRED_EXECUTION_ISOLATION_CAPABILITIES,
        verified=True,
        enforced=True,
        evidence_id="test-attestation",
        reason="test-only complete attestation",
    )


def test_release_profile_detection_covers_packaged_and_public_beta_profiles() -> None:
    assert release_profile_active({"LENGRVIS_COMMERCIAL_RELEASE": "true"}) is True
    assert release_profile_active({"LENGRVIS_RELEASE_CHANNEL": "public-beta"}) is True
    assert release_profile_active({"LENGRVIS_ENV": "development"}) is False


def test_release_execution_requires_complete_native_attestation() -> None:
    release_env = {"LENGRVIS_ENV": "production"}
    incomplete = ExecutionIsolationAttestation(
        provider="partial-host",
        platform="win32",
        capabilities={"job_object"},
        verified=True,
        enforced=True,
        evidence_id="partial",
    )

    assert arbitrary_execution_allowed(environ=release_env, attestation=incomplete) is False
    assert arbitrary_execution_allowed(environ=release_env, attestation=_complete_attestation()) is True


def test_release_configuration_blocks_unsafe_skill_and_generated_code_flags() -> None:
    settings = AppSettings(
        allow_unsafe_local_skill_execution=True,
        developer_writes_enabled=True,
    )
    issues = release_execution_configuration_issues(
        settings,
        environ={
            "LENGRVIS_RELEASE_BUILD": "true",
            "LENGRVIS_CODE_COMMAND": "python untrusted.py",
        },
        attestation=ExecutionIsolationAttestation(platform="win32"),
    )

    assert len(issues) == 3
    assert any("Local Python/PowerShell/Node/HTTP Skill execution" in issue for issue in issues)
    assert any("Developer generated-code/write execution" in issue for issue in issues)
    assert any("LENGRVIS_CODE_COMMAND" in issue for issue in issues)


def test_release_startup_assertion_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_ENV", "production")

    with pytest.raises(RuntimeError, match="Refusing unsafe release execution configuration"):
        assert_release_execution_configuration(AppSettings(developer_writes_enabled=True))


def test_release_settings_patch_cannot_enable_unsafe_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")
    monkeypatch.setattr(settings_service, "get_effective_settings", lambda: AppSettings())

    with pytest.raises(AppError) as exc_info:
        settings_service._validate_settings_patch({"allow_unsafe_local_skill_execution": True})

    assert exc_info.value.code == "execution_isolation_required"


def test_release_local_skill_context_override_cannot_launch_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_ENV", "production")
    marker = tmp_path / "executed.txt"
    (tmp_path / "handler.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    sandbox = SkillSandbox(tmp_path)
    execution = SkillExecution(type=SkillExecutionType.PYTHON, entry="handler.py")

    result = sandbox.execute(
        execution,
        {},
        {"allow_unsafe_local_skill_execution": True},
    )

    assert result["policy"] == "execution_isolation_required"
    assert result["execution_type"] == "python"
    assert marker.exists() is False


def test_release_developer_tool_policy_keeps_static_analysis_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_ENV", "production")

    assert allowed_tools_for_developer() == ("Read", "Grep", "Glob")
    with pytest.raises(ExecutionIsolationRequiredError):
        allowed_tools_for_developer(writes_enabled=True)
    with pytest.raises(ExecutionIsolationRequiredError):
        validate_allowed_tools(["Bash(python -m pytest:*)"])


def test_release_router_uses_in_process_path_for_read_only_code_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_ENV", "production")

    decision = route_engine("inspect repository files", requested_engine="developer")

    assert decision.selected_engine == "os"
    assert decision.rule == "developer_isolation_fallback"


def test_release_static_code_analysis_remains_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_ENV", "production")
    (tmp_path / "sample.py").write_text("TARGET = 1\n", encoding="utf-8")

    result = developer_tools.grep_files(
        {"path": str(tmp_path), "query": "TARGET", "pattern": "*.py"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_release_direct_developer_runtime_override_never_launches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_ENV", "production")

    async def fail_launch(*args, **kwargs):
        pytest.fail("release policy allowed an arbitrary developer runtime command")

    monkeypatch.setattr(lengrvis_code.asyncio, "create_subprocess_exec", fail_launch)
    summary = await run_lengrvis_code(
        "inspect repository",
        cwd=tmp_path,
        settings=AppSettings(),
        config=LengrvisCodeConfig(command=(sys.executable, "untrusted.py")),
    )

    assert "disabled in public beta/ga/release profiles" in summary.launch_error.lower()
    assert summary.runtime_health["available"] is False


def test_release_dev_test_run_blocks_process_but_allows_preview(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_ENV", "production")
    monkeypatch.setattr(
        developer_tools,
        "_run_test_foreground",
        lambda *args, **kwargs: pytest.fail("release policy allowed a developer subprocess"),
    )
    context = {"allowed_directories": [str(tmp_path)]}

    denied = developer_tools.test_run(
        {"cwd": str(tmp_path), "command": "python -m pytest"},
        context,
    )
    preview = developer_tools.test_run(
        {"cwd": str(tmp_path), "command": "python -m pytest", "dry_run": True},
        context,
    )

    assert denied["policy"] == "execution_isolation_required"
    assert denied["ok"] is False
    assert preview["dry_run"] is True
    assert preview["would_execute"] is True
