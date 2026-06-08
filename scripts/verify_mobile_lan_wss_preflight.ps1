[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$BackendHost = "",
    [int]$BackendPort = 0,
    [string]$PublicBaseUrl = "",
    [switch]$EnableLanTls,
    [string]$TlsCertFile = "",
    [string]$TlsKeyFile = "",
    [string]$EvidenceRoot = ""
)

$ErrorActionPreference = "Stop"
try {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = $utf8NoBom
    [Console]::InputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
}
catch {
}

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$issues = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]
$HttpsWssRequirement = "Token-bearing mobile LAN flows require HTTPS and WSS. Non-loopback HTTP/ws is blocked-path evidence only."
$NonEvidenceWarning = "This preflight does not use a phone, emulator, camera, QR scanner, or real WSS connection; it must not be recorded as real-device pass evidence."

function Get-EnvText([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ($null -eq $value) {
        return ""
    }
    return [string]$value
}

function Test-Configured([string]$Value) {
    return -not [string]::IsNullOrWhiteSpace($Value)
}

function Test-TruthyText([string]$Value) {
    return (Test-Configured $Value) -and $Value.Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
}

function Resolve-TextSetting([string]$ParameterValue, [string]$EnvName, [string]$DefaultValue) {
    if (Test-Configured $ParameterValue) {
        return [pscustomobject]@{ Value = $ParameterValue.Trim(); Source = "param" }
    }

    $envValue = Get-EnvText $EnvName
    if (Test-Configured $envValue) {
        return [pscustomobject]@{ Value = $envValue.Trim(); Source = "env:$EnvName" }
    }

    return [pscustomobject]@{ Value = $DefaultValue; Source = "default" }
}

function Resolve-PortSetting([int]$ParameterValue, [string]$EnvName, [int]$DefaultValue) {
    if ($ParameterValue -gt 0) {
        return [pscustomobject]@{ Value = $ParameterValue; Source = "param"; Valid = $true }
    }

    $envValue = Get-EnvText $EnvName
    if (Test-Configured $envValue) {
        $parsed = 0
        if ([int]::TryParse($envValue.Trim(), [ref]$parsed) -and $parsed -ge 1 -and $parsed -le 65535) {
            return [pscustomobject]@{ Value = $parsed; Source = "env:$EnvName"; Valid = $true }
        }
        return [pscustomobject]@{ Value = $DefaultValue; Source = "env:$EnvName"; Valid = $false; Raw = $envValue }
    }

    return [pscustomobject]@{ Value = $DefaultValue; Source = "default"; Valid = $true }
}

function Resolve-PreflightPath([string]$PathValue) {
    if (-not (Test-Configured $PathValue)) {
        return ""
    }

    $trimmed = $PathValue.Trim()
    if ([System.IO.Path]::IsPathRooted($trimmed)) {
        return $trimmed
    }
    return Join-Path $resolvedRoot $trimmed
}

function Test-WildcardHost([string]$HostName) {
    $raw = if ($null -eq $HostName) { "" } else { [string]$HostName }
    $normalized = $raw.Trim().Trim("[]".ToCharArray()).ToLowerInvariant()
    return $normalized -in @("", "*", "0.0.0.0", "::")
}

function Test-LoopbackHost([string]$HostName) {
    $raw = if ($null -eq $HostName) { "" } else { [string]$HostName }
    $normalized = $raw.Trim().Trim("[]".ToCharArray()).ToLowerInvariant()
    if ($normalized -in @("localhost", "127.0.0.1", "::1")) {
        return $true
    }
    if ($normalized.StartsWith("127.")) {
        return $true
    }

    $ipAddress = [System.Net.IPAddress]::None
    if ([System.Net.IPAddress]::TryParse($normalized, [ref]$ipAddress)) {
        return [System.Net.IPAddress]::IsLoopback($ipAddress)
    }
    return $false
}

function Get-RedactedHostLabel([string]$HostName) {
    if (Test-WildcardHost $HostName) {
        return "[bind-address]"
    }
    if (Test-LoopbackHost $HostName) {
        return "[loopback]"
    }
    return "[redacted-host]"
}

function Format-RedactedOrigin([string]$Scheme, [string]$HostName, [int]$Port) {
    return "${Scheme}://$(Get-RedactedHostLabel $HostName):$Port"
}

function Format-RedactedWebSocketUrl([string]$HttpOrigin, [string]$Path) {
    $uri = [Uri]$HttpOrigin
    $wsScheme = if ($uri.Scheme -eq "https") { "wss" } else { "ws" }
    $port = if ($uri.IsDefaultPort) { if ($wsScheme -eq "wss") { 443 } else { 80 } } else { $uri.Port }
    return "${wsScheme}://$(Get-RedactedHostLabel $uri.Host):$port$Path"
}

function Get-DisplayPath([string]$FullPath) {
    $rootPrefix = $resolvedRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
    if ($FullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $FullPath.Substring($rootPrefix.Length)
    }
    return Split-Path -Leaf $FullPath
}

function Resolve-Origin([string]$Value) {
    try {
        $uri = [Uri]$Value
    }
    catch {
        return [pscustomobject]@{ Ok = $false; Error = "Public backend URL is not a valid absolute URL."; Origin = "" }
    }

    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @("http", "https")) {
        return [pscustomobject]@{ Ok = $false; Error = "Public backend URL must start with http:// or https://."; Origin = "" }
    }
    if (Test-Configured $uri.UserInfo) {
        return [pscustomobject]@{ Ok = $false; Error = "Public backend URL must not include username/password information."; Origin = "" }
    }
    if ((Test-Configured $uri.AbsolutePath) -and $uri.AbsolutePath -ne "/") {
        return [pscustomobject]@{ Ok = $false; Error = "Public backend URL must be an origin only, for example https://192.168.1.20:9443."; Origin = "" }
    }
    if (Test-Configured $uri.Query -or Test-Configured $uri.Fragment) {
        return [pscustomobject]@{ Ok = $false; Error = "Public backend URL must not include query strings or fragments."; Origin = "" }
    }

    $origin = $uri.GetLeftPart([UriPartial]::Authority).TrimEnd("/")
    $port = if ($uri.IsDefaultPort) { if ($uri.Scheme -eq "https") { 443 } else { 80 } } else { $uri.Port }
    return [pscustomobject]@{
        Ok = $true
        Error = ""
        Origin = $origin
        Scheme = $uri.Scheme
        Host = $uri.Host
        Port = $port
        RedactedOrigin = Format-RedactedOrigin $uri.Scheme $uri.Host $port
    }
}

function Test-TlsMaterial([string]$CertPath, [string]$KeyPath, [string]$HostName) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        return [pscustomobject]@{
            Attempted = $false
            Ok = $false
            Error = "Python is required to validate the LAN TLS certificate/private key pair without starting the backend."
            CertificateFingerprintSha256 = ""
            HostOk = $false
        }
    }

    $pythonScript = @'
import hashlib
import ipaddress
import json
import ssl
import sys
from pathlib import Path

def _normalize_dns_name(value):
    return str(value or "").strip().strip("[]").rstrip(".").lower()

def _dns_name_matches(pattern, host_name):
    pattern = _normalize_dns_name(pattern)
    host_name = _normalize_dns_name(host_name)
    if not pattern or not host_name:
        return False
    if pattern == host_name:
        return True
    if not pattern.startswith("*."):
        return False
    suffix = pattern[1:]
    return host_name.endswith(suffix) and host_name.count(".") == pattern.count(".")

def _ip_matches(pattern, host_name):
    try:
        return ipaddress.ip_address(str(pattern).strip()) == ipaddress.ip_address(_normalize_dns_name(host_name))
    except ValueError:
        return False

def _cert_matches_host(decoded_cert, host_name):
    san = decoded_cert.get("subjectAltName", ())
    san_checked = False
    for key, value in san:
        if key == "DNS":
            san_checked = True
            if _dns_name_matches(value, host_name):
                return True
        elif key == "IP Address":
            san_checked = True
            if _ip_matches(value, host_name):
                return True
    if san_checked:
        return False
    for subject in decoded_cert.get("subject", ()):
        for key, value in subject:
            if key == "commonName" and _dns_name_matches(value, host_name):
                return True
    return False

cert_path = Path(sys.argv[1])
key_path = Path(sys.argv[2])
host_name = sys.argv[3].strip()
result = {"ok": False, "error": "", "certificate_fingerprint_sha256": "", "host_ok": False}
try:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    decode_cert = getattr(ssl, "_test_decode_cert", None)
    if decode_cert is None:
        decode_cert = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decode_cert is None:
        raise RuntimeError("certificate host validation unavailable")
    decoded_cert = decode_cert(str(cert_path))
    if not _cert_matches_host(decoded_cert, host_name):
        raise ssl.CertificateError("hostname mismatch")
    cert_text = cert_path.read_text(encoding="utf-8", errors="ignore")
    if "-----BEGIN CERTIFICATE-----" in cert_text:
        der = ssl.PEM_cert_to_DER_cert(cert_text)
    else:
        der = cert_path.read_bytes()
    result["ok"] = True
    result["host_ok"] = True
    result["certificate_fingerprint_sha256"] = hashlib.sha256(der).hexdigest()
except ssl.CertificateError:
    result["error"] = "certificate does not cover advertised public host"
except ssl.SSLError:
    result["error"] = "certificate/private key could not be parsed or do not match"
except OSError:
    result["error"] = "certificate/private key file could not be opened"
except Exception as exc:
    result["error"] = "certificate/private key validation failed (" + type(exc).__name__ + ")"
print(json.dumps(result, sort_keys=True))
'@

    $output = $pythonScript | & $python.Source - $CertPath $KeyPath $HostName 2>&1
    if ($LASTEXITCODE -ne 0) {
        return [pscustomobject]@{
            Attempted = $true
            Ok = $false
            Error = "certificate/private key validation command failed"
            CertificateFingerprintSha256 = ""
            HostOk = $false
        }
    }

    try {
        $parsed = ($output | Select-Object -Last 1) | ConvertFrom-Json
        return [pscustomobject]@{
            Attempted = $true
            Ok = [bool]$parsed.ok
            Error = if ($null -eq $parsed.error) { "" } else { [string]$parsed.error }
            CertificateFingerprintSha256 = if ($null -eq $parsed.certificate_fingerprint_sha256) { "" } else { [string]$parsed.certificate_fingerprint_sha256 }
            HostOk = [bool]$parsed.host_ok
        }
    }
    catch {
        return [pscustomobject]@{
            Attempted = $true
            Ok = $false
            Error = "certificate/private key validation output was not JSON"
            CertificateFingerprintSha256 = ""
            HostOk = $false
        }
    }
}

function New-SampleMobilePairingQrContent([string]$Origin, [string]$HostName, [int]$Port, [string]$Scheme, [object]$TransportSecurity) {
    $expiresAt = [DateTimeOffset]::UtcNow.AddMinutes(5).ToString("o")
    $payload = [ordered]@{
        type = "lengrvis.mobile_pairing"
        version = 1
        base_url = $Origin
        code = "redact"
        expires_at = $expiresAt
        expires_in = 300
        server = [ordered]@{
            host = $HostName
            port = $Port
            scheme = $Scheme
            origin = $Origin
            transport_security = $TransportSecurity
        }
        transport_security = $TransportSecurity
        https_enabled = ($Scheme -eq "https")
        trust_required = ($Scheme -eq "https")
    }
    $value = $payload | ConvertTo-Json -Depth 10 -Compress
    return [ordered]@{
        type = "lengrvis.mobile_pairing.qr"
        version = 1
        value = $value
        mime_type = "application/json"
        encoding = "utf-8"
        length = $value.Length
        payload = $payload
    }
}

function Test-MobilePairingQrShape([object]$QrContent) {
    $parsedValue = $QrContent.value | ConvertFrom-Json
    $requiredPayloadFields = @("type", "version", "base_url", "code", "expires_at", "expires_in", "server")
    $payloadFieldNames = @($QrContent.payload.Keys)
    $missingPayloadFields = @($requiredPayloadFields | Where-Object { $_ -notin $payloadFieldNames })
    $serverFieldNames = @($QrContent.payload.server.Keys)
    $missingServerFields = @("host", "port", "scheme", "origin", "transport_security") | Where-Object { $_ -notin $serverFieldNames }

    return [ordered]@{
        wrapper_type = $QrContent.type
        payload_type = $QrContent.payload.type
        version = $QrContent.version
        mime_type = $QrContent.mime_type
        encoding = $QrContent.encoding
        value_is_json = ($null -ne $parsedValue)
        value_length_matches = ($QrContent.length -eq $QrContent.value.Length)
        code_is_six_alnum = ([string]$QrContent.payload.code -match "^[a-z0-9]{6}$")
        required_payload_fields_present = ($missingPayloadFields.Count -eq 0)
        required_server_fields_present = ($missingServerFields.Count -eq 0)
        websocket_approvals_url_redacted = Format-RedactedWebSocketUrl ([string]$QrContent.payload.base_url) "/ws/mobile/approvals"
        websocket_remote_input_url_redacted = Format-RedactedWebSocketUrl ([string]$QrContent.payload.base_url) "/ws/remote/input"
        pairing_code_redacted = $true
        base_url_redacted = (Resolve-Origin ([string]$QrContent.payload.base_url)).RedactedOrigin
        transport_security_status = [string]$QrContent.payload.transport_security.status
        transport_security_tls_ready = [bool]$QrContent.payload.transport_security.tls_ready
    }
}

$backendHostSetting = Resolve-TextSetting $BackendHost "LENGRVIS_BACKEND_HOST" "127.0.0.1"
$backendPortSetting = Resolve-PortSetting $BackendPort "LENGRVIS_BACKEND_PORT" 8000
$publicBaseUrlSetting = Resolve-TextSetting $PublicBaseUrl "LENGRVIS_LAN_PUBLIC_BASE_URL" ""
$certSetting = Resolve-TextSetting $TlsCertFile "LENGRVIS_LAN_TLS_CERT_FILE" ""
$keySetting = Resolve-TextSetting $TlsKeyFile "LENGRVIS_LAN_TLS_KEY_FILE" ""
$tlsEnabledRaw = Get-EnvText "LENGRVIS_LAN_TLS_ENABLED"
$tlsEnabled = [bool]$EnableLanTls -or (Test-TruthyText $tlsEnabledRaw) -or (Test-Configured $certSetting.Value) -or (Test-Configured $keySetting.Value)
$tlsEnabledSource = if ($EnableLanTls) { "param" } elseif (Test-Configured $tlsEnabledRaw) { "env:LENGRVIS_LAN_TLS_ENABLED" } elseif ((Test-Configured $certSetting.Value) -or (Test-Configured $keySetting.Value)) { "cert-env-present" } else { "default" }

if (-not $backendPortSetting.Valid) {
    $issues.Add("LENGRVIS_BACKEND_PORT must be a number from 1 to 65535; current value is not safe to advertise in a QR payload.")
}

$derivedScheme = if ($tlsEnabled) { "https" } else { "http" }
$originInput = $publicBaseUrlSetting.Value
$originSource = $publicBaseUrlSetting.Source
if (-not (Test-Configured $originInput)) {
    if (Test-WildcardHost $backendHostSetting.Value) {
        $issues.Add("0.0.0.0 is a bind address, not a phone-reachable QR host. LENGRVIS_BACKEND_HOST=$($backendHostSetting.Value) needs LENGRVIS_LAN_PUBLIC_BASE_URL=https://<LAN-IP-or-DNS>:$($backendPortSetting.Value).")
    }
    else {
        $originInput = "${derivedScheme}://$($backendHostSetting.Value):$($backendPortSetting.Value)"
        $originSource = "derived:$($backendHostSetting.Source)+$($backendPortSetting.Source)"
    }
}

$originResult = $null
if (Test-Configured $originInput) {
    $originResult = Resolve-Origin $originInput
    if (-not $originResult.Ok) {
        $issues.Add($originResult.Error)
    }
}

$certPath = Resolve-PreflightPath $certSetting.Value
$keyPath = Resolve-PreflightPath $keySetting.Value
$certPresent = (Test-Configured $certPath) -and (Test-Path -LiteralPath $certPath -PathType Leaf)
$keyPresent = (Test-Configured $keyPath) -and (Test-Path -LiteralPath $keyPath -PathType Leaf)
$tlsValidation = [pscustomobject]@{ Attempted = $false; Ok = $false; Error = ""; CertificateFingerprintSha256 = ""; HostOk = $false }

if ($originResult -and $originResult.Ok) {
    if (Test-WildcardHost $originResult.Host) {
        $issues.Add("LENGRVIS_LAN_PUBLIC_BASE_URL must advertise a real phone-reachable host, not 0.0.0.0, ::, or *.")
    }
    if (Test-LoopbackHost $originResult.Host) {
        $issues.Add("The advertised QR host is loopback-only. That is useful for local development, but it cannot be release LAN/WSS or real Android evidence.")
    }
    if ($originResult.Scheme -ne "https") {
        $issues.Add("Unsafe mobile LAN transport: $HttpsWssRequirement Set LENGRVIS_LAN_PUBLIC_BASE_URL to an https:// origin before generating phone pairing evidence.")
    }
    if ($originResult.Scheme -eq "https" -and -not $tlsEnabled) {
        $issues.Add("LENGRVIS_LAN_PUBLIC_BASE_URL is https://, but LAN TLS is not enabled for the direct backend preflight. Set LENGRVIS_LAN_TLS_ENABLED=true and cert/key env vars.")
    }
}

if ($tlsEnabled) {
    if (-not (Test-Configured $certSetting.Value)) {
        $issues.Add("LENGRVIS_LAN_TLS_CERT_FILE is required when collecting HTTPS/WSS mobile LAN evidence.")
    }
    elseif (-not $certPresent) {
        $issues.Add("LENGRVIS_LAN_TLS_CERT_FILE points to a missing certificate file.")
    }

    if (-not (Test-Configured $keySetting.Value)) {
        $issues.Add("LENGRVIS_LAN_TLS_KEY_FILE is required when collecting HTTPS/WSS mobile LAN evidence.")
    }
    elseif (-not $keyPresent) {
        $issues.Add("LENGRVIS_LAN_TLS_KEY_FILE points to a missing private key file.")
    }

    if ($certPresent -and $keyPresent -and $originResult -and $originResult.Ok) {
        $tlsValidation = Test-TlsMaterial $certPath $keyPath $originResult.Host
        if (-not $tlsValidation.Ok) {
            $issues.Add("LAN TLS certificate/private key validation failed: $($tlsValidation.Error)")
        }
    }
}
else {
    $warnings.Add("LAN TLS is disabled. The script will record a blocked-path summary, not readiness evidence.")
}

$qrShape = $null
if ($originResult -and $originResult.Ok) {
    $transportSecurity = [ordered]@{
        status = if ($originResult.Scheme -eq "https" -and $tlsEnabled -and $certPresent -and $keyPresent -and $tlsValidation.Ok) { "https_ready_preflight" } else { "https_wss_preflight_blocked" }
        scheme = $originResult.Scheme
        websocket_scheme = if ($originResult.Scheme -eq "https") { "wss" } else { "ws" }
        https_enabled = ($originResult.Scheme -eq "https")
        tls_ready = ($originResult.Scheme -eq "https" -and $tlsEnabled -and $certPresent -and $keyPresent -and $tlsValidation.Ok)
        cert_configured = (Test-Configured $certSetting.Value)
        key_configured = (Test-Configured $keySetting.Value)
        cert_present = $certPresent
        key_present = $keyPresent
        requires_trust = ($originResult.Scheme -eq "https")
        trust_required = ($originResult.Scheme -eq "https")
        trust_model = if ($originResult.Scheme -eq "https") { "local_certificate" } else { "none" }
    }
    $qrContent = New-SampleMobilePairingQrContent $originResult.Origin $originResult.Host $originResult.Port $originResult.Scheme $transportSecurity
    $qrShape = Test-MobilePairingQrShape $qrContent
    if (-not $qrShape.required_payload_fields_present -or -not $qrShape.required_server_fields_present -or -not $qrShape.value_length_matches -or -not $qrShape.code_is_six_alnum) {
        $issues.Add("Generated sample QR payload shape does not match desktop/mobile pairing expectations.")
    }
}

if ([string]::IsNullOrWhiteSpace($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $resolvedRoot ".tmp\mobile-lan-wss-preflight"
}
elseif (-not [System.IO.Path]::IsPathRooted($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $resolvedRoot $EvidenceRoot
}

$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $EvidenceRoot "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$summaryPath = Join-Path $runRoot "evidence-summary.redacted.json"
$displaySummaryPath = Get-DisplayPath $summaryPath

$manualRealDeviceEvidenceTemplate = [ordered]@{
    template_status = "manual_real_device_evidence_required"
    real_device_result = "uncollected"
    preflight_blocked = ($issues.Count -gt 0)
    may_be_recorded_as = "preflight/config evidence only"
    must_not_be_recorded_as = "real-device pass evidence"
    blocked_reason_redacted = @($issues)
    required_redactions = @(
        "mobile token",
        "grant token",
        "pairing code",
        "hostnames/IP addresses unless explicitly local-only",
        "private local paths",
        "device names",
        "nested model-action args",
        "selectors",
        "task secrets",
        "support-only notes"
    )
    fields = [ordered]@{
        candidate = ""
        device_identity_redacted = ""
        android_or_emulator_profile = ""
        same_lan_path_redacted = ""
        https_origin_redacted = if ($originResult -and $originResult.Ok) { $originResult.RedactedOrigin } else { "" }
        approval_wss_origin_redacted = if ($originResult -and $originResult.Ok) { Format-RedactedWebSocketUrl $originResult.Origin "/ws/mobile/approvals" } else { "" }
        remote_input_wss_origin_redacted = if ($originResult -and $originResult.Ok) { Format-RedactedWebSocketUrl $originResult.Origin "/ws/remote/input" } else { "" }
        certificate_trust_path = ""
        certificate_fingerprint_sha256_or_ca = if (Test-Configured $tlsValidation.CertificateFingerprintSha256) { $tlsValidation.CertificateFingerprintSha256 } else { "" }
        camera_qr_path_evidence = "uncollected"
        actual_device_https_wss_evidence = "uncollected"
        approval_artifact_review = "uncollected"
        remote_screen_artifact_review = "uncollected"
        remote_input_artifact_review = "uncollected"
        artifact_redaction_review = "uncollected"
    }
}

$summary = [ordered]@{
    result = if ($issues.Count -eq 0) { "ready_for_manual_real_device_evidence" } else { "blocked" }
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    redacted_evidence_summary_path = $displaySummaryPath
    non_evidence_warning = $NonEvidenceWarning
    https_wss_requirement = $HttpsWssRequirement
    backend = [ordered]@{
        host_source = $backendHostSetting.Source
        host_redacted = Get-RedactedHostLabel $backendHostSetting.Value
        port = $backendPortSetting.Value
        port_source = $backendPortSetting.Source
        public_base_url_source = $originSource
        public_base_url_redacted = if ($originResult -and $originResult.Ok) { $originResult.RedactedOrigin } else { "" }
        websocket_approvals_url_redacted = if ($originResult -and $originResult.Ok) { Format-RedactedWebSocketUrl $originResult.Origin "/ws/mobile/approvals" } else { "" }
        websocket_remote_input_url_redacted = if ($originResult -and $originResult.Ok) { Format-RedactedWebSocketUrl $originResult.Origin "/ws/remote/input" } else { "" }
    }
    lan_tls = [ordered]@{
        enabled = $tlsEnabled
        enabled_source = $tlsEnabledSource
        cert_env = "LENGRVIS_LAN_TLS_CERT_FILE"
        key_env = "LENGRVIS_LAN_TLS_KEY_FILE"
        cert_configured = (Test-Configured $certSetting.Value)
        key_configured = (Test-Configured $keySetting.Value)
        cert_present = $certPresent
        key_present = $keyPresent
        cert_file_label = if (Test-Configured $certPath) { Split-Path -Leaf $certPath } else { "" }
        key_file_label = if (Test-Configured $keyPath) { Split-Path -Leaf $keyPath } else { "" }
        tls_material_validation_attempted = $tlsValidation.Attempted
        tls_material_valid = $tlsValidation.Ok
        tls_host_valid = $tlsValidation.HostOk
        certificate_fingerprint_sha256 = $tlsValidation.CertificateFingerprintSha256
    }
    qr_payload_shape = $qrShape
    issues = @($issues)
    warnings = @($warnings)
    next_manual_evidence_needed = @(
        "Real Android device or documented emulator identity",
        "Real camera/QR scan path if scan-to-pair is claimed",
        "Actual HTTPS/WSS connection from that device",
        "Certificate source, fingerprint or CA identity, and explicit Android/emulator trust path",
        "Approval/remote screen/remote input screenshots or logs with tokens, pairing codes, hostnames, private paths, and device names redacted unless explicitly local-only"
    )
    manual_real_device_evidence_template = $manualRealDeviceEvidenceTemplate
}

$summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $summaryPath -Encoding utf8

Write-Host "Mobile LAN/WSS prerequisite preflight"
Write-Host $HttpsWssRequirement
Write-Host $NonEvidenceWarning
Write-Host "Redacted evidence summary path: $displaySummaryPath"

if ($warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "Warnings:" -ForegroundColor Yellow
    foreach ($warning in $warnings) {
        Write-Host " - $warning" -ForegroundColor Yellow
    }
}

if ($issues.Count -gt 0) {
    Write-Host ""
    Write-Host "[blocked] Mobile LAN/WSS prerequisites are not safe to use for release evidence:" -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host " - $issue" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "Beginner next steps:" -ForegroundColor Yellow
    Write-Host " - Advertise an https:// LAN origin with LENGRVIS_LAN_PUBLIC_BASE_URL, not 0.0.0.0 or localhost." -ForegroundColor Yellow
    Write-Host " - Set LENGRVIS_LAN_TLS_ENABLED=true plus LENGRVIS_LAN_TLS_CERT_FILE and LENGRVIS_LAN_TLS_KEY_FILE." -ForegroundColor Yellow
    Write-Host " - Restart the backend after changing TLS env/config, rerun this preflight, then collect real phone/emulator WSS evidence separately." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "[ready] LAN/WSS prerequisites are ready for manual Android/emulator evidence collection." -ForegroundColor Green
Write-Host "This is still not a real-device pass; record it only as prereq/config evidence." -ForegroundColor Yellow
exit 0
