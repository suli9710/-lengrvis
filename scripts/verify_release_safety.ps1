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

from app.commerce.licensing import (
    _load_public_key,
    parse_license,
    parse_revocation_manifest,
)
from app.config import AppSettings, _configured, _find_config_file, _load_dotenv, _load_yaml, env_value


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
paid_profile = commercial_release or plan in {"pro", "professional", "team", "team-self-hosted", "enterprise"}
public_key = str(env_value(env, "LENGRVIS_LICENSE_PUBLIC_KEY") or "").strip()
license_token = str(env_value(env, "LENGRVIS_LICENSE_KEY") or "").strip()
revocations = str(env_value(env, "LENGRVIS_LICENSE_REVOCATIONS") or "").strip()

for private_name in ("LENGRVIS_LICENSE_PRIVATE_KEY", "LENGRVIS_LICENSE_SIGNING_KEY"):
    if _configured(env_value(env, private_name)):
        license_issues.append(
            f"Release runtime must not contain {private_name}; keep issuer private keys offline."
        )

if paid_profile and not public_key:
    license_issues.append(
        "Paid/commercial release profiles require LENGRVIS_LICENSE_PUBLIC_KEY."
    )
if plan in {"pro", "professional", "team", "team-self-hosted", "enterprise"} and not commercial_release:
    license_issues.append(
        "Paid plan release profiles must set LENGRVIS_COMMERCIAL_RELEASE=true so environment plan overrides cannot bypass licensing."
    )

if public_key:
    try:
        _load_public_key(public_key)
    except Exception:
        license_issues.append("LENGRVIS_LICENSE_PUBLIC_KEY is not a valid Ed25519 public key.")

if license_token and public_key:
    try:
        parsed_license = parse_license(license_token, public_key)
        if not parsed_license.license_id:
            license_issues.append("Configured production license is missing license_id.")
        if not parsed_license.issuer:
            license_issues.append("Configured production license is missing issuer.")
    except Exception:
        license_issues.append("LENGRVIS_LICENSE_KEY did not pass Ed25519 verification.")

if revocations:
    if not public_key:
        license_issues.append(
            "LENGRVIS_LICENSE_REVOCATIONS requires LENGRVIS_LICENSE_PUBLIC_KEY."
        )
    else:
        try:
            parse_revocation_manifest(revocations, public_key)
        except Exception:
            license_issues.append(
                "LENGRVIS_LICENSE_REVOCATIONS did not pass Ed25519 verification."
            )

print(json.dumps({
    "allow_mock_fallback": allow_mock_fallback,
    "allow_mock_fallback_source": allow_mock_source,
    "strict_state_machine": strict_state_machine,
    "strict_state_machine_source": strict_source,
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

Write-Host "Release safety verification passed: allow_mock_fallback=false and strict_state_machine=true. Commercial license secrets are offline-only and paid profiles have a valid verifier."
