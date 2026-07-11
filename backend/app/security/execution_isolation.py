from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

REQUIRED_EXECUTION_ISOLATION_CAPABILITIES = frozenset(
    {
        "appcontainer",
        "restricted_token",
        "job_object",
        "network_broker",
    }
)
RELEASE_ENVIRONMENT_VALUES = frozenset(
    {
        "beta",
        "candidate",
        "ga",
        "prod",
        "production",
        "public-beta",
        "public_beta",
        "rc",
        "release",
    }
)
RELEASE_ENVIRONMENT_NAMES = ("LENGRVIS_ENV", "APP_ENV", "ENVIRONMENT", "LENGRVIS_RELEASE_CHANNEL")
RELEASE_BOOLEAN_NAMES = ("LENGRVIS_COMMERCIAL_RELEASE", "LENGRVIS_PUBLIC_BETA", "LENGRVIS_RELEASE_BUILD")
READ_ONLY_CODE_ANALYSIS_TOOLS = frozenset({"Read", "Grep", "Glob"})
WINDOWS_ISOLATION_ATTESTATION_MODULE = "app.security.windows_execution_isolation_host"


class ExecutionIsolationRequiredError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionIsolationAttestation:
    provider: str = "unavailable"
    platform: str = field(default_factory=lambda: sys.platform)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    schema_version: int = 1
    verified: bool = False
    enforced: bool = False
    evidence_id: str = ""
    reason: str = "No trusted Windows execution-isolation host is installed."

    @property
    def missing_capabilities(self) -> frozenset[str]:
        return REQUIRED_EXECUTION_ISOLATION_CAPABILITIES - self.capabilities

    @property
    def complete(self) -> bool:
        return bool(
            self.platform == "win32"
            and self.schema_version == 1
            and self.verified
            and self.enforced
            and self.provider.strip()
            and self.evidence_id.strip()
            and not self.missing_capabilities
        )

    def public_payload(self) -> dict[str, Any]:
        return {
            "attested": self.complete,
            "provider": self.provider,
            "platform": self.platform,
            "schema_version": self.schema_version,
            "verified": self.verified,
            "enforced": self.enforced,
            "capabilities": sorted(self.capabilities),
            "missing_capabilities": sorted(self.missing_capabilities),
            "evidence_id": self.evidence_id,
            "reason": self.reason,
        }


def release_profile_active(environ: Mapping[str, str] | None = None) -> bool:
    source = environ if environ is not None else os.environ
    if any(_truthy(source.get(name)) for name in RELEASE_BOOLEAN_NAMES):
        return True
    return any(
        str(source.get(name) or "").strip().casefold() in RELEASE_ENVIRONMENT_VALUES
        for name in RELEASE_ENVIRONMENT_NAMES
    )


def current_execution_isolation_attestation() -> ExecutionIsolationAttestation:
    if sys.platform != "win32":
        return ExecutionIsolationAttestation(
            platform=sys.platform,
            reason="Release execution isolation is supported only by the Windows isolation host.",
        )
    try:
        host = importlib.import_module(WINDOWS_ISOLATION_ATTESTATION_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != WINDOWS_ISOLATION_ATTESTATION_MODULE:
            return ExecutionIsolationAttestation(reason="The Windows isolation host failed to load.")
        return ExecutionIsolationAttestation()
    except Exception:  # noqa: BLE001 - broad-exception-boundary: unhealthy security providers fail closed.
        return ExecutionIsolationAttestation(reason="The Windows isolation host failed to load.")

    attest = getattr(host, "attest_current_process_tree", None)
    if not callable(attest):
        return ExecutionIsolationAttestation(reason="The Windows isolation host has no attestation entrypoint.")
    try:
        raw = attest()
    except Exception:  # noqa: BLE001 - broad-exception-boundary: provider failures must never enable execution.
        return ExecutionIsolationAttestation(reason="The Windows isolation host could not attest enforcement.")
    return _coerce_attestation(raw)


def arbitrary_execution_allowed(
    *,
    environ: Mapping[str, str] | None = None,
    attestation: ExecutionIsolationAttestation | None = None,
) -> bool:
    if not release_profile_active(environ):
        return True
    active = attestation if attestation is not None else current_execution_isolation_attestation()
    return active.complete


def arbitrary_execution_denial(
    operation: str,
    *,
    environ: Mapping[str, str] | None = None,
    attestation: ExecutionIsolationAttestation | None = None,
) -> dict[str, Any] | None:
    if arbitrary_execution_allowed(environ=environ, attestation=attestation):
        return None
    active = attestation if attestation is not None else current_execution_isolation_attestation()
    return {
        "error": (
            f"{operation} is disabled in public Beta/GA/release profiles until a trusted Windows host attests "
            "AppContainer, restricted-token, Job Object, and network-broker enforcement."
        ),
        "policy": "execution_isolation_required",
        "operation": operation,
        "required_capabilities": sorted(REQUIRED_EXECUTION_ISOLATION_CAPABILITIES),
        "missing_capabilities": sorted(active.missing_capabilities),
    }


def assert_arbitrary_execution_allowed(operation: str) -> None:
    denial = arbitrary_execution_denial(operation)
    if denial is not None:
        raise ExecutionIsolationRequiredError(str(denial["error"]))


def constrain_developer_allowed_tools(
    allowed_tools: Sequence[str],
    *,
    allow_write_tools: bool,
    environ: Mapping[str, str] | None = None,
    attestation: ExecutionIsolationAttestation | None = None,
) -> tuple[str, ...]:
    normalized = tuple(str(tool).strip() for tool in allowed_tools if str(tool).strip())
    if arbitrary_execution_allowed(environ=environ, attestation=attestation):
        return normalized
    if allow_write_tools:
        raise ExecutionIsolationRequiredError(_execution_isolation_error("Generated-code write execution"))
    return tuple(tool for tool in normalized if tool.split("(", 1)[0] in READ_ONLY_CODE_ANALYSIS_TOOLS)


def assert_developer_allowed_tools_safe(
    allowed_tools: Sequence[str],
    *,
    allow_write_tools: bool,
    environ: Mapping[str, str] | None = None,
    attestation: ExecutionIsolationAttestation | None = None,
) -> None:
    normalized = tuple(str(tool).strip() for tool in allowed_tools if str(tool).strip())
    constrained = constrain_developer_allowed_tools(
        normalized,
        allow_write_tools=allow_write_tools,
        environ=environ,
        attestation=attestation,
    )
    if constrained != normalized:
        raise ExecutionIsolationRequiredError(_execution_isolation_error("Developer subprocess tool execution"))


def release_execution_configuration_issues(
    settings: Any,
    *,
    environ: Mapping[str, str] | None = None,
    attestation: ExecutionIsolationAttestation | None = None,
) -> list[str]:
    source = environ if environ is not None else os.environ
    if not release_profile_active(source):
        return []
    active = attestation if attestation is not None else current_execution_isolation_attestation()
    if active.complete:
        return []

    issues: list[str] = []
    if bool(getattr(settings, "allow_unsafe_local_skill_execution", False)):
        issues.append(
            _execution_isolation_error("Local Python/PowerShell/Node/HTTP Skill execution")
            + " Disable LENGRVIS_ALLOW_UNSAFE_LOCAL_SKILL_EXECUTION for this release profile."
        )
    if bool(getattr(settings, "developer_writes_enabled", False)):
        issues.append(
            _execution_isolation_error("Developer generated-code/write execution")
            + " Disable LENGRVIS_DEVELOPER_WRITES_ENABLED for this release profile."
        )
    runtime_overrides = [
        name
        for name in ("LENGRVIS_CODE_COMMAND", "LENGRVIS_CODE_VENDOR_ROOT")
        if str(source.get(name) or "").strip()
    ]
    if runtime_overrides:
        issues.append(
            _execution_isolation_error("Developer runtime command/vendor override")
            + " Remove these release environment overrides: "
            + ", ".join(runtime_overrides)
            + "."
        )
    return issues


def assert_release_execution_configuration(settings: Any) -> None:
    issues = release_execution_configuration_issues(settings)
    if issues:
        raise RuntimeError("Refusing unsafe release execution configuration: " + " ".join(issues))


def _coerce_attestation(raw: Any) -> ExecutionIsolationAttestation:
    if isinstance(raw, ExecutionIsolationAttestation):
        if raw.platform != sys.platform:
            return ExecutionIsolationAttestation(reason="The Windows isolation attestation platform did not match.")
        return raw
    if not isinstance(raw, Mapping):
        return ExecutionIsolationAttestation(reason="The Windows isolation host returned an invalid attestation.")

    capabilities_raw = raw.get("capabilities")
    if not isinstance(capabilities_raw, list | tuple | set | frozenset):
        capabilities_raw = ()
    capabilities = frozenset(
        str(item).strip().casefold()
        for item in capabilities_raw
        if str(item).strip()
    )
    return ExecutionIsolationAttestation(
        provider=str(raw.get("provider") or "").strip(),
        platform=sys.platform,
        capabilities=capabilities,
        schema_version=_strict_int(raw.get("schema_version"), default=0),
        verified=raw.get("verified") is True,
        enforced=raw.get("enforced") is True,
        evidence_id=str(raw.get("evidence_id") or "").strip(),
        reason=str(raw.get("reason") or "").strip(),
    )


def _strict_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _execution_isolation_error(operation: str) -> str:
    return (
        f"{operation} cannot be enabled in a public Beta/GA/release profile without attested "
        "AppContainer, restricted-token, Job Object, and network-broker enforcement."
    )


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}
