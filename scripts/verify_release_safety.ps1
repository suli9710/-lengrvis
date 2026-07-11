[CmdletBinding()]
param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$issues = New-Object System.Collections.Generic.List[string]

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    $issues.Add("Python is required to verify release safety settings.")
}
else {
    $previousPythonPath = $env:PYTHONPATH
    $backendRoot = Join-Path $resolvedRoot "backend"
    $pathSeparator = [System.IO.Path]::PathSeparator
    Push-Location $resolvedRoot
    try {
        $pythonPathEntries = @($backendRoot, $resolvedRoot)
        if (-not [string]::IsNullOrWhiteSpace($previousPythonPath)) {
            $pythonPathEntries += $previousPythonPath
        }
        $env:PYTHONPATH = ($pythonPathEntries -join $pathSeparator)

        $pythonScript = @'
import json
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from app.commerce.licensing import (
    _load_public_key,
    parse_license,
    parse_revocation_manifest,
)
from app.config import AppSettings, _configured, _find_config_file, _load_dotenv, _load_yaml, env_value
from app.security.execution_isolation import (
    current_execution_isolation_attestation,
    release_execution_configuration_issues,
)


def sections(config):
    for name in ("llm", "privacy", "paths", "orchestration", "perception", "transport"):
        value = config.get(name, {})
        yield name, value if isinstance(value, dict) else {}


def flag(config, env, env_key, yaml_key, default):
    raw_env = env_value(env, env_key)
    if _configured(raw_env):
        return coerce_bool(raw_env), f"env:{env_key}"

    for section_name, section in sections(config):
        if yaml_key in section and _configured(section.get(yaml_key)):
            return coerce_bool(section.get(yaml_key)), f"config:{section_name}.{yaml_key}"

    return default, "default"


def coerce_bool(raw):
    if isinstance(raw, bool):
        return raw
    return str(raw).lower() in {"1", "true", "yes", "on"}


config_path = _find_config_file("config.yaml", "LENGRVIS_CONFIG_FILE")
env_path = _find_config_file(".env", "LENGRVIS_ENV_FILE")
config = _load_yaml(config_path) if config_path else {}
env_file = _load_dotenv(env_path) if env_path else {}
env = {**env_file, **os.environ}
settings = AppSettings.from_sources()
execution_isolation = current_execution_isolation_attestation()
execution_isolation_issues = release_execution_configuration_issues(
    settings,
    environ={**env, "LENGRVIS_RELEASE_BUILD": "true"},
    attestation=execution_isolation,
)

allow_mock_fallback, allow_mock_source = flag(
    config,
    env,
    "LENGRVIS_ALLOW_MOCK_FALLBACK",
    "allow_mock_fallback",
    False,
)
strict_state_machine, strict_source = flag(
    config,
    env,
    "LENGRVIS_STRICT_STATE_MACHINE",
    "strict_state_machine",
    False,
)

license_issues = []
plan = str(settings.plan or "free").strip().lower()
commercial_release = coerce_bool(env_value(env, "LENGRVIS_COMMERCIAL_RELEASE") or False)
paid_profile = commercial_release or plan in {"pro", "professional", "max", "team", "team-self-hosted", "enterprise"}
public_key = str(env_value(env, "LENGRVIS_LICENSE_PUBLIC_KEY") or "").strip()
license_token = str(env_value(env, "LENGRVIS_LICENSE_KEY") or "").strip()
revocations = str(env_value(env, "LENGRVIS_LICENSE_REVOCATIONS") or "").strip()
activation_base_url = str(env_value(env, "LENGRVIS_ACTIVATION_BASE_URL") or "").strip()
activation_insecure_http = coerce_bool(env_value(env, "LENGRVIS_ACTIVATION_ALLOW_INSECURE_HTTP") or False)
activation_require_strong_device_proof = coerce_bool(
    env_value(env, "LENGRVIS_ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF") or False
)
activation_deployment_evidence = {
    name: str(env_value(env, name) or "").strip()
    for name in (
        "LENGRVIS_ACTIVATION_REVERSE_PROXY_EVIDENCE",
        "LENGRVIS_ACTIVATION_RATE_LIMIT_EVIDENCE",
        "LENGRVIS_ACTIVATION_AUDIT_EVIDENCE",
        "LENGRVIS_ACTIVATION_OPERATIONS_EVIDENCE",
    )
}
revocation_max_age_raw = str(env_value(env, "LENGRVIS_LICENSE_REVOCATION_MAX_AGE_SECONDS") or "").strip()
cloud_quota_enforced = env_value(env, "LENGRVIS_CLOUD_QUOTA_ENFORCED")
cloud_quota_overrides = [
    name for name in (
        "LENGRVIS_CLOUD_QUOTA_WINDOW_HOURS",
        "LENGRVIS_CLOUD_QUOTA_MAX_TOKENS",
        "LENGRVIS_CLOUD_QUOTA_MAX_CALLS",
        "LENGRVIS_CLOUD_QUOTA_MAX_COST_USD",
    )
    if env_value(env, name) not in (None, "")
]

for private_name in (
    "LENGRVIS_LICENSE_PRIVATE_KEY",
    "LENGRVIS_LICENSE_SIGNING_KEY",
    "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY",
    "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY_FILE",
    "LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE",
    "LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE_FILE",
):
    if _configured(env_value(env, private_name)):
        license_issues.append(
            f"Release runtime must not contain {private_name}; keep issuer private keys offline."
        )

if paid_profile and not public_key:
    license_issues.append(
        "Paid/commercial release profiles require LENGRVIS_LICENSE_PUBLIC_KEY."
    )
if plan in {"pro", "professional", "max", "team", "team-self-hosted", "enterprise"} and not commercial_release:
    license_issues.append(
        "Paid plan release profiles must set LENGRVIS_COMMERCIAL_RELEASE=true so environment plan overrides cannot bypass licensing."
    )

if public_key:
    try:
        _load_public_key(public_key)
    except Exception:
        license_issues.append("LENGRVIS_LICENSE_PUBLIC_KEY is not a valid Ed25519 public key.")

parsed_runtime_license = None
offline_paid_license_requires_revocations = False
if license_token and public_key:
    try:
        parsed_runtime_license = parse_license(license_token, public_key)
        if not parsed_runtime_license.license_id:
            license_issues.append("Configured production license is missing license_id.")
        if not parsed_runtime_license.issuer:
            license_issues.append("Configured production license is missing issuer.")
        activation = parsed_runtime_license.payload.get("activation")
        activation_source = ""
        if isinstance(activation, dict):
            activation_source = str(activation.get("source") or "").strip().lower()
        offline_paid_license_requires_revocations = (
            commercial_release
            and parsed_runtime_license.plan.value != "free"
            and not parsed_runtime_license.subscription_id
            and activation_source != "activation_server"
        )
    except Exception:
        license_issues.append("LENGRVIS_LICENSE_KEY did not pass Ed25519 verification.")

parsed_revocations = None
if revocations:
    if not public_key:
        license_issues.append(
            "LENGRVIS_LICENSE_REVOCATIONS requires LENGRVIS_LICENSE_PUBLIC_KEY."
        )
    else:
        try:
            parsed_revocations = parse_revocation_manifest(revocations, public_key)
        except Exception:
            license_issues.append(
                "LENGRVIS_LICENSE_REVOCATIONS did not pass Ed25519 verification."
            )

if commercial_release:
    if activation_insecure_http:
        license_issues.append(
            "Commercial release profiles must not set LENGRVIS_ACTIVATION_ALLOW_INSECURE_HTTP=true."
        )
    if activation_base_url:
        parsed_activation = urlparse(activation_base_url)
        if parsed_activation.scheme != "https":
            license_issues.append(
                "Commercial release profiles must use an HTTPS LENGRVIS_ACTIVATION_BASE_URL."
            )
        if not activation_require_strong_device_proof:
            license_issues.append(
                "Commercial activation profiles must set LENGRVIS_ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF=true."
            )
        missing_activation_evidence = [
            name for name, value in activation_deployment_evidence.items() if not value
        ]
        if missing_activation_evidence:
            license_issues.append(
                "Commercial activation profiles require reverse-proxy, rate-limit, audit, and operations evidence: "
                + ", ".join(missing_activation_evidence)
            )
    if offline_paid_license_requires_revocations:
        if not revocations:
            license_issues.append(
                "Commercial offline paid license profiles require LENGRVIS_LICENSE_REVOCATIONS."
            )
        elif parsed_revocations is not None:
            generated_at = parsed_revocations.generated_at
            if generated_at is None:
                license_issues.append("Commercial revocation manifests must include generated_at.")
            else:
                try:
                    max_age_seconds = max(60, min(30 * 24 * 60 * 60, int(revocation_max_age_raw or "604800")))
                except ValueError:
                    max_age_seconds = 604800
                now = datetime.now(UTC)
                if generated_at > now + timedelta(minutes=5):
                    license_issues.append("Commercial revocation manifests must not be dated in the future.")
                elif now - generated_at > timedelta(seconds=max_age_seconds):
                    license_issues.append("Commercial revocation manifests are stale.")
    if cloud_quota_enforced is not None and str(cloud_quota_enforced).strip().lower() in {"0", "false", "no", "off"}:
        license_issues.append(
            "Commercial release profiles must not set LENGRVIS_CLOUD_QUOTA_ENFORCED=false."
        )
    if cloud_quota_overrides:
        license_issues.append(
            "Commercial release profiles must not use LENGRVIS_CLOUD_QUOTA_* limit overrides: "
            + ", ".join(cloud_quota_overrides)
        )

print(json.dumps({
    "allow_mock_fallback": allow_mock_fallback,
    "allow_mock_fallback_source": allow_mock_source,
    "strict_state_machine": strict_state_machine,
    "strict_state_machine_source": strict_source,
    "execution_isolation": execution_isolation.public_payload(),
    "execution_isolation_issues": execution_isolation_issues,
    "license_issues": license_issues,
}, sort_keys=True))
'@

        $output = $pythonScript | & $python.Source - 2>&1
        if ($LASTEXITCODE -ne 0) {
            $issues.Add("Failed to load release safety settings: $($output -join "`n")")
        }
        else {
            $jsonLine = $output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1
            try {
                $settings = $jsonLine | ConvertFrom-Json
                if ($settings.allow_mock_fallback) {
                    $issues.Add("Release/production builds must not enable LENGRVIS_ALLOW_MOCK_FALLBACK=true (source: $($settings.allow_mock_fallback_source)).")
                }
                if (-not $settings.strict_state_machine) {
                    $issues.Add("Release/production gates require strict_state_machine=true; set LENGRVIS_STRICT_STATE_MACHINE=true or privacy.strict_state_machine: true in config.yaml (source: $($settings.strict_state_machine_source)).")
                }
                foreach ($executionIssue in @($settings.execution_isolation_issues)) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$executionIssue)) {
                        $issues.Add([string]$executionIssue)
                    }
                }
                foreach ($licenseIssue in @($settings.license_issues)) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$licenseIssue)) {
                        $issues.Add([string]$licenseIssue)
                    }
                }
            }
            catch {
                $issues.Add("Release safety settings output was not valid JSON: $jsonLine")
            }
        }
    }
    finally {
        if ($null -eq $previousPythonPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONPATH = $previousPythonPath
        }
        Pop-Location
    }
}

if ($issues.Count -gt 0) {
    Write-Host "Release safety verification failed:" -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host " - $issue" -ForegroundColor Red
    }
    exit 1
}

Write-Host "Release safety verification passed: allow_mock_fallback=false and strict_state_machine=true. Arbitrary local/code execution is either disabled or backed by a complete trusted Windows isolation attestation. Commercial license secrets are offline-only and paid profiles have a valid verifier."
