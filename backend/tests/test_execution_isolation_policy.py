from __future__ import annotations

import base64
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
from app.security import execution_isolation
from app.security.execution_isolation import (
    REQUIRED_EXECUTION_ISOLATION_CAPABILITIES,
    ExecutionIsolationAttestation,
    ExecutionIsolationRequiredError,
    arbitrary_execution_allowed,
    assert_release_execution_configuration,
    canonical_execution_isolation_attestation_bytes,
    current_execution_isolation_attestation,
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
        issued_at_utc="2026-07-17T00:00:00+00:00",
        expires_at_utc="2026-07-17T00:05:00+00:00",
        host_binary_sha256=f"sha256:{'1' * 64}",
        policy_sha256=f"sha256:{'2' * 64}",
        key_fingerprint=f"sha256:{'3' * 64}",
        reason="test-only complete attestation",
    )


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _signed_isolation_host(
    private_key: Ed25519PrivateKey,
    *,
    challenge_mutator=None,
    issued_delta: timedelta = timedelta(seconds=-1),
    expires_delta: timedelta = timedelta(minutes=2),
):
    class SignedHost:
        @staticmethod
        def attest_current_process_tree(challenge):  # noqa: ANN001, ANN205
            signed_challenge = dict(challenge)
            if challenge_mutator is not None:
                signed_challenge = challenge_mutator(signed_challenge)
            now = datetime.now(UTC)
            payload = {
                "schema_version": "lengrvis-windows-execution-isolation-v1",
                "provider": "test-native-host",
                "platform": "win32",
                "capabilities": sorted(REQUIRED_EXECUTION_ISOLATION_CAPABILITIES),
                "enforced": True,
                "evidence_id": "test-native-attestation",
                "issued_at_utc": (now + issued_delta).isoformat(),
                "expires_at_utc": (now + expires_delta).isoformat(),
                "host_binary_sha256": f"sha256:{'4' * 64}",
                "policy_sha256": f"sha256:{'5' * 64}",
                "challenge": signed_challenge,
                "reason": "test native host enforced all required boundaries",
            }
            signature = private_key.sign(canonical_execution_isolation_attestation_bytes(payload))
            return {
                "payload": payload,
                "signature": f"ed25519:{_b64url(signature)}",
            }

    return SignedHost()


def _install_signed_isolation_host(monkeypatch: pytest.MonkeyPatch, host) -> None:
    private_key = getattr(host, "_private_key", None)
    if private_key is not None:
        raise AssertionError("private keys must never be attached to the host object")
    monkeypatch.setattr(execution_isolation.sys, "platform", "win32")
    monkeypatch.setattr(
        execution_isolation.importlib,
        "import_module",
        lambda _name: host,
    )


def _set_isolation_public_key(
    monkeypatch: pytest.MonkeyPatch,
    private_key: Ed25519PrivateKey,
) -> None:
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        "LENGRVIS_WINDOWS_ISOLATION_ATTESTATION_PUBLIC_KEY",
        f"ed25519:{_b64url(public_key)}",
    )
    monkeypatch.setenv(
        "LENGRVIS_WINDOWS_ISOLATION_HOST_SHA256",
        f"sha256:{'4' * 64}",
    )
    monkeypatch.setenv(
        "LENGRVIS_WINDOWS_ISOLATION_POLICY_SHA256",
        f"sha256:{'5' * 64}",
    )


def test_current_execution_isolation_requires_signed_fresh_process_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    _set_isolation_public_key(monkeypatch, private_key)
    _install_signed_isolation_host(
        monkeypatch,
        _signed_isolation_host(private_key),
    )

    attestation = current_execution_isolation_attestation()

    assert attestation.complete is True
    assert attestation.key_fingerprint.startswith("sha256:")
    assert attestation.host_binary_sha256 == f"sha256:{'4' * 64}"


def test_current_execution_isolation_rejects_replayed_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    _set_isolation_public_key(monkeypatch, private_key)
    _install_signed_isolation_host(
        monkeypatch,
        _signed_isolation_host(
            private_key,
            challenge_mutator=lambda challenge: {**challenge, "process_id": 1},
        ),
    )

    attestation = current_execution_isolation_attestation()

    assert attestation.complete is False
    assert "challenge" in attestation.reason.lower()


def test_current_execution_isolation_rejects_expired_signed_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    _set_isolation_public_key(monkeypatch, private_key)
    _install_signed_isolation_host(
        monkeypatch,
        _signed_isolation_host(
            private_key,
            issued_delta=timedelta(minutes=-3),
            expires_delta=timedelta(minutes=-1),
        ),
    )

    attestation = current_execution_isolation_attestation()

    assert attestation.complete is False
    assert "expired" in attestation.reason.lower()


@pytest.mark.parametrize(
    ("environment_name", "value", "reason_fragment"),
    [
        ("LENGRVIS_WINDOWS_ISOLATION_HOST_SHA256", "", "host binary digest"),
        ("LENGRVIS_WINDOWS_ISOLATION_POLICY_SHA256", "", "policy digest"),
        ("LENGRVIS_WINDOWS_ISOLATION_HOST_SHA256", f"sha256:{'6' * 64}", "host binary digest"),
        ("LENGRVIS_WINDOWS_ISOLATION_POLICY_SHA256", f"sha256:{'6' * 64}", "policy digest"),
    ],
)
def test_current_execution_isolation_rejects_missing_or_mismatched_release_pins(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    value: str,
    reason_fragment: str,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    _set_isolation_public_key(monkeypatch, private_key)
    if value:
        monkeypatch.setenv(environment_name, value)
    else:
        monkeypatch.delenv(environment_name)
    _install_signed_isolation_host(
        monkeypatch,
        _signed_isolation_host(private_key),
    )

    attestation = current_execution_isolation_attestation()

    assert attestation.complete is False
    assert reason_fragment in attestation.reason.casefold()


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
