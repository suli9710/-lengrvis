from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import re
import secrets
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

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
WINDOWS_ISOLATION_ATTESTATION_PUBLIC_KEY_ENV = "LENGRVIS_WINDOWS_ISOLATION_ATTESTATION_PUBLIC_KEY"
WINDOWS_ISOLATION_HOST_SHA256_ENV = "LENGRVIS_WINDOWS_ISOLATION_HOST_SHA256"
WINDOWS_ISOLATION_POLICY_SHA256_ENV = "LENGRVIS_WINDOWS_ISOLATION_POLICY_SHA256"
WINDOWS_ISOLATION_ATTESTATION_PAYLOAD_SCHEMA = "lengrvis-windows-execution-isolation-v1"
WINDOWS_ISOLATION_ATTESTATION_MAX_TTL_SECONDS = 300
WINDOWS_ISOLATION_ATTESTATION_CLOCK_SKEW_SECONDS = 30
_SHA256_LABEL_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_WINDOWS_ISOLATION_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "provider",
        "platform",
        "capabilities",
        "enforced",
        "evidence_id",
        "issued_at_utc",
        "expires_at_utc",
        "host_binary_sha256",
        "policy_sha256",
        "challenge",
        "reason",
    }
)


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
    issued_at_utc: str = ""
    expires_at_utc: str = ""
    host_binary_sha256: str = ""
    policy_sha256: str = ""
    key_fingerprint: str = ""
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
            and self.issued_at_utc.strip()
            and self.expires_at_utc.strip()
            and _SHA256_LABEL_RE.fullmatch(self.host_binary_sha256)
            and _SHA256_LABEL_RE.fullmatch(self.policy_sha256)
            and _SHA256_LABEL_RE.fullmatch(self.key_fingerprint)
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
            "issued_at_utc": self.issued_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "host_binary_sha256": self.host_binary_sha256,
            "policy_sha256": self.policy_sha256,
            "key_fingerprint": self.key_fingerprint,
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
    pin_error = _execution_isolation_pin_error()
    if pin_error:
        return ExecutionIsolationAttestation(reason=pin_error)
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
    challenge = _execution_isolation_challenge()
    try:
        raw = attest(challenge)
    except Exception:  # noqa: BLE001 - broad-exception-boundary: provider failures must never enable execution.
        return ExecutionIsolationAttestation(reason="The Windows isolation host could not attest enforcement.")
    return _coerce_attestation(raw, challenge=challenge)


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
        name for name in ("LENGRVIS_CODE_COMMAND", "LENGRVIS_CODE_VENDOR_ROOT") if str(source.get(name) or "").strip()
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


def canonical_execution_isolation_attestation_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the exact UTF-8 bytes a trusted native host must sign."""

    if not isinstance(payload, Mapping):
        raise ValueError("Windows isolation attestation payload must be an object.")
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Windows isolation attestation payload is not canonicalizable.") from exc


def _coerce_attestation(
    raw: Any,
    *,
    challenge: Mapping[str, Any],
) -> ExecutionIsolationAttestation:
    if isinstance(raw, ExecutionIsolationAttestation):
        return ExecutionIsolationAttestation(
            reason="The Windows isolation host returned an unsigned in-process attestation."
        )
    if not isinstance(raw, Mapping):
        return ExecutionIsolationAttestation(reason="The Windows isolation host returned an invalid attestation.")

    payload = raw.get("payload")
    signature_text = str(raw.get("signature") or "").strip()
    if not isinstance(payload, Mapping) or frozenset(payload) != _WINDOWS_ISOLATION_PAYLOAD_KEYS:
        return ExecutionIsolationAttestation(reason="The Windows isolation host returned an invalid signed payload.")

    public_key_text = str(os.environ.get(WINDOWS_ISOLATION_ATTESTATION_PUBLIC_KEY_ENV) or "").strip()
    if not public_key_text:
        return ExecutionIsolationAttestation(
            reason="The trusted Windows isolation attestation public key is not configured."
        )

    try:
        validated = _validate_windows_isolation_payload(payload, challenge=challenge)
        public_key_bytes = _decode_ed25519_value(
            public_key_text,
            expected_length=32,
            label="public key",
        )
        signature_bytes = _decode_ed25519_value(
            signature_text,
            expected_length=64,
            label="signature",
        )
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature_bytes,
            canonical_execution_isolation_attestation_bytes(payload),
        )
    except InvalidSignature:
        return ExecutionIsolationAttestation(reason="The Windows isolation attestation signature verification failed.")
    except ValueError as exc:
        return ExecutionIsolationAttestation(reason=str(exc))

    return ExecutionIsolationAttestation(
        provider=validated["provider"],
        platform=validated["platform"],
        capabilities=frozenset(validated["capabilities"]),
        schema_version=1,
        verified=True,
        enforced=True,
        evidence_id=validated["evidence_id"],
        issued_at_utc=validated["issued_at_utc"],
        expires_at_utc=validated["expires_at_utc"],
        host_binary_sha256=validated["host_binary_sha256"],
        policy_sha256=validated["policy_sha256"],
        key_fingerprint=_sha256_label(public_key_bytes),
        reason=validated["reason"],
    )


def _execution_isolation_challenge() -> dict[str, Any]:
    return {
        "nonce": secrets.token_urlsafe(32),
        "process_id": os.getpid(),
        "parent_process_id": os.getppid(),
        "expected_host_binary_sha256": str(os.environ.get(WINDOWS_ISOLATION_HOST_SHA256_ENV) or "").strip(),
        "expected_policy_sha256": str(os.environ.get(WINDOWS_ISOLATION_POLICY_SHA256_ENV) or "").strip(),
    }


def _validate_windows_isolation_payload(
    payload: Mapping[str, Any],
    *,
    challenge: Mapping[str, Any],
) -> dict[str, Any]:
    if payload.get("schema_version") != WINDOWS_ISOLATION_ATTESTATION_PAYLOAD_SCHEMA:
        raise ValueError("The Windows isolation attestation schema is invalid.")
    if payload.get("platform") != "win32" or sys.platform != "win32":
        raise ValueError("The Windows isolation attestation platform did not match.")
    if payload.get("enforced") is not True:
        raise ValueError("The Windows isolation attestation did not prove enforcement.")
    if payload.get("challenge") != dict(challenge):
        raise ValueError("The Windows isolation attestation challenge did not match.")

    provider = str(payload.get("provider") or "").strip()
    evidence_id = str(payload.get("evidence_id") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not provider or len(provider) > 128:
        raise ValueError("The Windows isolation attestation provider is invalid.")
    if not evidence_id or len(evidence_id) > 256:
        raise ValueError("The Windows isolation attestation evidence id is invalid.")
    if len(reason) > 512:
        raise ValueError("The Windows isolation attestation reason is invalid.")

    capabilities_raw = payload.get("capabilities")
    if not isinstance(capabilities_raw, list) or not capabilities_raw:
        raise ValueError("The Windows isolation attestation capabilities are invalid.")
    capabilities = [str(item).strip().casefold() for item in capabilities_raw if str(item).strip()]
    if capabilities != sorted(set(capabilities)):
        raise ValueError("The Windows isolation attestation capabilities are not canonical.")
    if REQUIRED_EXECUTION_ISOLATION_CAPABILITIES - frozenset(capabilities):
        raise ValueError("The Windows isolation attestation is missing required capabilities.")

    host_binary_sha256 = str(payload.get("host_binary_sha256") or "").strip()
    policy_sha256 = str(payload.get("policy_sha256") or "").strip()
    if not _SHA256_LABEL_RE.fullmatch(host_binary_sha256):
        raise ValueError("The Windows isolation host binary digest is invalid.")
    if not _SHA256_LABEL_RE.fullmatch(policy_sha256):
        raise ValueError("The Windows isolation policy digest is invalid.")
    if host_binary_sha256 != str(challenge.get("expected_host_binary_sha256") or ""):
        raise ValueError("The Windows isolation host binary digest did not match the release pin.")
    if policy_sha256 != str(challenge.get("expected_policy_sha256") or ""):
        raise ValueError("The Windows isolation policy digest did not match the release pin.")

    issued_at = _parse_attestation_time(payload.get("issued_at_utc"), "issued_at_utc")
    expires_at = _parse_attestation_time(payload.get("expires_at_utc"), "expires_at_utc")
    now = datetime.now(UTC)
    if issued_at > now.timestamp() + WINDOWS_ISOLATION_ATTESTATION_CLOCK_SKEW_SECONDS:
        raise ValueError("The Windows isolation attestation was issued in the future.")
    if expires_at <= now.timestamp():
        raise ValueError("The Windows isolation attestation has expired.")
    if expires_at <= issued_at:
        raise ValueError("The Windows isolation attestation expiry is invalid.")
    if expires_at - issued_at > WINDOWS_ISOLATION_ATTESTATION_MAX_TTL_SECONDS:
        raise ValueError("The Windows isolation attestation lifetime is too long.")

    return {
        "provider": provider,
        "platform": "win32",
        "capabilities": capabilities,
        "evidence_id": evidence_id,
        "issued_at_utc": str(payload.get("issued_at_utc") or "").strip(),
        "expires_at_utc": str(payload.get("expires_at_utc") or "").strip(),
        "host_binary_sha256": host_binary_sha256,
        "policy_sha256": policy_sha256,
        "reason": reason,
    }


def _parse_attestation_time(value: Any, label: str) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"The Windows isolation attestation {label} is missing.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"The Windows isolation attestation {label} is invalid.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"The Windows isolation attestation {label} must include a timezone.")
    return parsed.astimezone(UTC).timestamp()


def _decode_ed25519_value(value: str, *, expected_length: int, label: str) -> bytes:
    normalized = str(value or "").strip()
    if not normalized.startswith("ed25519:"):
        raise ValueError(f"The Windows isolation attestation {label} must use the ed25519: prefix.")
    encoded = normalized.removeprefix("ed25519:")
    if not _B64URL_RE.fullmatch(encoded):
        raise ValueError(f"The Windows isolation attestation {label} is invalid.")
    padded = encoded + ("=" * ((4 - len(encoded) % 4) % 4))
    try:
        decoded = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"The Windows isolation attestation {label} is invalid.") from exc
    if len(decoded) != expected_length:
        raise ValueError(f"The Windows isolation attestation {label} length is invalid.")
    return decoded


def _sha256_label(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _execution_isolation_pin_error() -> str:
    host_digest = str(os.environ.get(WINDOWS_ISOLATION_HOST_SHA256_ENV) or "").strip()
    if not _SHA256_LABEL_RE.fullmatch(host_digest):
        return "The trusted Windows isolation host binary digest release pin is missing or invalid."
    policy_digest = str(os.environ.get(WINDOWS_ISOLATION_POLICY_SHA256_ENV) or "").strip()
    if not _SHA256_LABEL_RE.fullmatch(policy_digest):
        return "The trusted Windows isolation policy digest release pin is missing or invalid."
    return ""


def _execution_isolation_error(operation: str) -> str:
    return (
        f"{operation} cannot be enabled in a public Beta/GA/release profile without attested "
        "AppContainer, restricted-token, Job Object, and network-broker enforcement."
    )


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}
