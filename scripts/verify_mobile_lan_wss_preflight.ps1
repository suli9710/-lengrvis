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
$ManualEvidenceChecklistWarning = "Manual real-device evidence remains uncollected until redacted phone/emulator artifacts prove camera QR, actual HTTPS/WSS, certificate trust, grant revoke/expiry, and screenshot/log review."
$FailClosedRealDeviceStatus = "uncollected_fail_closed"

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

function Redact-DisplayLabel([string]$Label) {
    if (-not (Test-Configured $Label)) {
        return ""
    }
    $text = [string]$Label
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{4,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "(?i)\b(?:contoso|acme|customer)[A-Za-z0-9]*", "[redacted-org]")
    $text = [regex]::Replace($text, "(?i)([?&](?:token|api[_-]?key|client_secret|secret|password|code)=)[^&\s]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)\bhttps?://[^/\s\\]+", "https://[redacted-host]")
    $text = [regex]::Replace($text, "(?i)\bwss?://[^/\s\\]+", "wss://[redacted-host]")
    $text = [regex]::Replace($text, "\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-host]")
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|client_secret|secret|password|code)=[A-Za-z0-9._-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|client_secret|secret|password|code)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
    return $text
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
        return Redact-DisplayLabel $FullPath.Substring($rootPrefix.Length)
    }
    return Redact-DisplayLabel (Split-Path -Leaf $FullPath)
}

function Add-MarkdownChecklistBlock([System.Collections.Generic.List[string]]$Lines, [string]$Title, [object]$Entry) {
    $Lines.Add("### $Title")
    $Lines.Add("")
    $Lines.Add("- Status: " + [string]$Entry["status"])
    if ($Entry.Contains("required_when")) {
        $Lines.Add("- Required when: " + [string]$Entry["required_when"])
    }
    if ($Entry.Contains("why_it_matters")) {
        $Lines.Add("- Why this matters: " + [string]$Entry["why_it_matters"])
    }
    if ($Entry.Contains("beginner_steps")) {
        $Lines.Add("- Do this on the phone/emulator:")
        foreach ($item in @($Entry["beginner_steps"])) {
            $Lines.Add("  - [ ] " + [string]$item)
        }
    }
    $Lines.Add("- Attach after real device/emulator run:")
    foreach ($item in @($Entry["must_attach"])) {
        $Lines.Add("  - [ ] " + [string]$item)
    }
    if ($Entry.Contains("pass_evidence_must_show")) {
        $Lines.Add("- Pass evidence must show: " + [string]$Entry["pass_evidence_must_show"])
    }
    if ($Entry.Contains("reviewer_check")) {
        $Lines.Add("- Reviewer check: " + [string]$Entry["reviewer_check"])
    }
    $Lines.Add("- Overclaim guard: " + [string]$Entry["overclaim_guard"])
    $Lines.Add("")
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
        websocket_remote_screen_url_redacted = Format-RedactedWebSocketUrl ([string]$QrContent.payload.base_url) "/ws/remote/screen"
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
$checklistPath = Join-Path $runRoot "real-device-evidence-checklist.redacted.md"
$displaySummaryPath = Get-DisplayPath $summaryPath
$displayChecklistPath = Get-DisplayPath $checklistPath

$manualRealDeviceEvidenceTemplate = [ordered]@{
    template_status = "manual_real_device_evidence_required"
    real_device_result = "uncollected"
    real_device_evidence_status = $FailClosedRealDeviceStatus
    real_device_evidence_collected = $false
    no_phone_preflight_claim = "not_real_device_pass"
    preflight_blocked = ($issues.Count -gt 0)
    may_be_recorded_as = "preflight/config evidence only"
    must_not_be_recorded_as = "real-device pass evidence"
    blocked_reason_redacted = @($issues)
    claim_controls = [ordered]@{
        real_device_pass_claim_allowed = $false
        pass_claim_unlock_condition = "Attach reviewed real Android/emulator evidence for the scoped scenarios outside this preflight."
        preflight_ready_is_pass = $false
    }
    artifact_collection_rules = [ordered]@{
        shareable_packet = "Use redacted artifacts only; host/IP labels should stay [redacted-host] unless the packet is explicitly local-only."
        local_only_raw_values = "Keep raw LAN IPs, hostnames, device names, pairing codes, mobile tokens, grant tokens, and Authorization headers outside tracked source and outside shareable artifacts."
        token_bearing_urls = "Never paste token-bearing URLs; record only redacted origins and whether HTTPS/WSS connected."
        review_required_before_pass_claim = $true
    }
    operator_collection_order = @(
        "Run this preflight and keep both redacted outputs with the candidate run notes.",
        "On the phone/emulator, confirm the device is on the same LAN path as the backend and can reach the advertised HTTPS origin.",
        "Trust the certificate on the exact Android/emulator profile before pairing, then record the trust path and certificate fingerprint or CA.",
        "Use the real camera QR scanner when scan-to-pair is claimed; pasted payloads must be labeled as fallback-only evidence.",
        "Collect approval WSS, remote screen WSS, and remote input WSS evidence separately so a partial pass cannot be mistaken for full mobile LAN/WSS.",
        "Exercise remote input revoke and expiry from the device-visible UI before marking remote input evidence collected.",
        "Collect real phone/emulator artifacts for the scoped scenarios only after HTTPS/WSS and device certificate trust are configured.",
        "Fill every applicable uncollected field with reviewed, redacted evidence labels; leave out-of-scope fields as uncollected with a separate waiver.",
        "Ask a reviewer to confirm redactions before any screenshot, video, log, or trace is shared or used for a release/demo claim.",
        "Keep claim_controls.real_device_pass_claim_allowed=false until the reviewed real-device packet exists outside this preflight."
    )
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
        remote_screen_wss_origin_redacted = if ($originResult -and $originResult.Ok) { Format-RedactedWebSocketUrl $originResult.Origin "/ws/remote/screen" } else { "" }
        remote_input_wss_origin_redacted = if ($originResult -and $originResult.Ok) { Format-RedactedWebSocketUrl $originResult.Origin "/ws/remote/input" } else { "" }
        certificate_trust_path = ""
        certificate_fingerprint_sha256_or_ca = if (Test-Configured $tlsValidation.CertificateFingerprintSha256) { $tlsValidation.CertificateFingerprintSha256 } else { "" }
        camera_qr_path_evidence = "uncollected"
        actual_device_https_wss_evidence = "uncollected"
        approval_wss_evidence = "uncollected"
        approval_artifact_review = "uncollected"
        remote_screen_wss_evidence = "uncollected"
        remote_screen_artifact_review = "uncollected"
        remote_input_wss_evidence = "uncollected"
        remote_input_artifact_review = "uncollected"
        certificate_trust_evidence = "uncollected"
        remote_input_grant_revoke_evidence = "uncollected"
        remote_input_grant_expiry_evidence = "uncollected"
        grant_revoke_expiry_artifact_review = "uncollected"
        artifact_redaction_review = "uncollected"
    }
    real_device_collection_checklist = [ordered]@{
        camera_qr = [ordered]@{
            status = "uncollected"
            required_when = "scan-to-pair or camera QR pairing is claimed"
            why_it_matters = "A generated QR or parser smoke only proves software shape; the release claim needs a device camera/emulator scan path."
            beginner_steps = @(
                "Open the mobile app pairing screen on the target Android device or emulator.",
                "Point the real camera or emulator virtual camera at the desktop QR; do not switch to paste/manual entry for this evidence.",
                "After the scan, wait for paired state before saving screenshots or notes."
            )
            must_attach = @(
                "Redacted phone/emulator screenshot or video of the camera scanner reading the desktop QR",
                "Paired-state screenshot or reviewed log note after scan, with pairing code and tokens redacted"
            )
            pass_evidence_must_show = "The scan came from the camera QR path and the app reached paired state without exposing the pairing code or token."
            reviewer_check = "Confirm no raw QR payload, pairing code, token, host/IP, or private device name appears in shareable artifacts."
            overclaim_guard = "QR generation, payload parser smoke, pasted payload, or this preflight alone is not camera QR evidence."
        }
        actual_https_wss = [ordered]@{
            status = "uncollected"
            why_it_matters = "HTTPS-ready metadata is only a prerequisite; the device must actually connect over HTTPS/WSS."
            beginner_steps = @(
                "Pair from the phone/emulator using the advertised HTTPS origin.",
                "Trigger one approval, open remote screen, and enable remote input only if it is in scope.",
                "Record each WSS path separately so a screen-only run is not mistaken for approval or input evidence."
            )
            must_attach = @(
                "Device-originated HTTPS API reachability or pairing confirmation over the advertised origin",
                "Approval WebSocket connected over WSS from the device",
                "Remote screen WebSocket connected over WSS from the device",
                "Remote input WebSocket connected over WSS from the device when input is in scope"
            )
            pass_evidence_must_show = "The target device, not a desktop TestClient or parser, opened token-bearing HTTPS/WSS paths."
            reviewer_check = "Confirm all token-bearing URLs are redacted and every WebSocket path uses wss://, not ws://."
            overclaim_guard = "HTTPS-ready metadata or a redacted WSS URL shape is prerequisite/config evidence only."
        }
        approval_wss = [ordered]@{
            status = "uncollected"
            required_when = "mobile approval evidence is claimed"
            why_it_matters = "Approval notifications are safety-critical and must be proven on the actual mobile approval WebSocket."
            beginner_steps = @(
                "Keep the paired mobile app open on the approvals screen.",
                "Create one benign desktop approval and wait for it to appear on the device.",
                "Approve one benign request and reject one benign request while recording redacted mobile and backend evidence."
            )
            must_attach = @(
                "Mobile screenshot/video showing the approval received from /ws/mobile/approvals over WSS",
                "Approve and reject outcomes tied to the same redacted candidate run",
                "Backend or audit log note confirming no token appears in the WebSocket URL"
            )
            pass_evidence_must_show = "Approval events arrived on the device through wss:// and decisions round-tripped without raw task secrets."
            reviewer_check = "Inspect mobile approval artifacts for nested model-action args, local paths, selectors, tokens, values, and support-only notes."
            overclaim_guard = "Backend approval tests, local smoke, or a redacted approval URL shape are not actual approval WSS evidence."
        }
        remote_screen_wss = [ordered]@{
            status = "uncollected"
            required_when = "remote screen or read-only mobile desktop viewing is claimed"
            why_it_matters = "Remote screen needs proof that visible frames render over WSS on the target device and stay read-only by default."
            beginner_steps = @(
                "Open the mobile Remote screen from the paired device.",
                "Wait for a visible desktop frame and connection state.",
                "Capture the transport notice and read-only/default input state in the same run."
            )
            must_attach = @(
                "Mobile screenshot/video showing /ws/remote/screen connected over WSS",
                "Visible remote frame, connection state, and transport notice from the phone/emulator",
                "Read-only state before any remote input grant is claimed"
            )
            pass_evidence_must_show = "The device displays a live remote frame over wss:// and input is not active without a grant."
            reviewer_check = "Confirm screenshots/logs do not expose private desktop content beyond the approved test fixture."
            overclaim_guard = "Source-level UI or backend WebSocket tests are not real-device remote screen WSS evidence."
        }
        remote_input_wss = [ordered]@{
            status = "uncollected"
            required_when = "remote input is claimed"
            why_it_matters = "Remote input is high risk; the device must prove grant-scoped WSS input, desktop approval, and safe stop behavior."
            beginner_steps = @(
                "Create a remote input grant from desktop for the paired device.",
                "Claim the grant on the phone/emulator and confirm remaining time is visible.",
                "Send one benign click/key event and verify the desktop approval or dry-run record before continuing."
            )
            must_attach = @(
                "Mobile screenshot/video showing /ws/remote/input connected over WSS with remaining grant time",
                "Desktop approval or dry-run record for the benign input event",
                "Evidence that input is disabled before grant and after revoke/expiry"
            )
            pass_evidence_must_show = "Only the grant-scoped remote input token can use wss:// remote input, and the UI clearly returns to read-only when the grant ends."
            reviewer_check = "Confirm grant tokens, selectors, coordinates, local paths, and task-sensitive values are redacted."
            overclaim_guard = "A remote input grant smoke or backend TestClient close test is not real-device remote input WSS evidence."
        }
        certificate_trust = [ordered]@{
            status = "uncollected"
            configured_fingerprint_sha256_or_ca = if (Test-Configured $tlsValidation.CertificateFingerprintSha256) { $tlsValidation.CertificateFingerprintSha256 } else { "" }
            why_it_matters = "The backend certificate may parse and match the host while Android/emulator still does not trust it."
            beginner_steps = @(
                "Record whether the device uses a public CA, installed local CA, user-installed certificate, emulator network security config, or test profile trust.",
                "Before trusting, record the expected failure or blocked state if the scenario includes trust failure.",
                "After trusting, repeat pairing and WSS checks on the same device profile."
            )
            must_attach = @(
                "Certificate source plus SHA-256 fingerprint or CA identity",
                "Explicit Android/emulator trust path used by the test device",
                "Trust-failure or trust-success screenshot/log note with tokens and hosts redacted as required"
            )
            pass_evidence_must_show = "The exact Android/emulator profile used for the run trusts the certificate path used by the advertised HTTPS/WSS origin."
            reviewer_check = "Confirm the certificate fingerprint or CA label matches the candidate run and raw hostnames/IPs stay local-only."
            overclaim_guard = "Cert/key parse and host coverage checks do not prove Android/emulator trust."
        }
        remote_input_grant_revoke_expiry = [ordered]@{
            status = "uncollected"
            why_it_matters = "Remote input must stop reliably when the user, desktop, token, or time limit ends the grant."
            beginner_steps = @(
                "While remote input is active, tap the mobile end-control and verify input becomes disabled/read-only.",
                "Create a second grant, revoke it from desktop or revoke the mobile device, and watch the phone/emulator disconnect.",
                "Use a short grant or controlled test build to observe expiry and failed reconnect with the expired grant."
            )
            must_attach = @(
                "Mobile end-control revoke returns the UI to read-only and closes/stops input",
                "Desktop/device revoke closes/stops input and prevents further events",
                "Grant expiry disables input and cannot reconnect with the expired grant",
                "Token expiry or device revoke closes approval/screen/input WebSockets where in scope"
            )
            pass_evidence_must_show = "After revoke or expiry, the phone/emulator cannot send further remote input and any WebSocket closes or rejects safely."
            reviewer_check = "Confirm the run captures both mobile-visible UI state and backend/device event evidence."
            overclaim_guard = "Backend/client smoke coverage does not replace visible real-device revoke/expiry evidence."
        }
        screenshot_log_review = [ordered]@{
            status = "uncollected"
            why_it_matters = "The evidence packet is only shareable after someone checks that screenshots, videos, logs, and traces are redacted."
            beginner_steps = @(
                "Open each screenshot, video, app log, backend log, and proxy trace that will be attached.",
                "Search for tokens, pairing codes, raw hostnames/IPs, private paths, selectors, support-only notes, and task secrets.",
                "Write a short reviewer note naming the artifact labels that were checked."
            )
            must_attach = @(
                "Reviewed approval, remote screen, remote input, backend, mobile, and proxy artifacts used for the claim",
                "A note that shareable screenshots/logs contain no mobile token, grant token, pairing code, private path, nested model-action args, selector, support-only note, device name, or task secret"
            )
            pass_evidence_must_show = "Every artifact used for the mobile real-device claim was reviewed and redacted before sharing."
            reviewer_check = "A reviewer other than the collector should confirm the redaction note for release/demo evidence."
            overclaim_guard = "Do not call artifacts shareable or passed until this review is filled."
        }
    }
}

$summary = [ordered]@{
    result = if ($issues.Count -eq 0) { "ready_for_manual_real_device_collection_only" } else { "blocked" }
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    redacted_evidence_summary_path = $displaySummaryPath
    real_device_evidence_status = $FailClosedRealDeviceStatus
    real_device_evidence_collected = $false
    no_phone_preflight_claim = "not_real_device_pass"
    non_evidence_warning = $NonEvidenceWarning
    manual_evidence_checklist_warning = $ManualEvidenceChecklistWarning
    https_wss_requirement = $HttpsWssRequirement
    backend = [ordered]@{
        host_source = $backendHostSetting.Source
        host_redacted = Get-RedactedHostLabel $backendHostSetting.Value
        port = $backendPortSetting.Value
        port_source = $backendPortSetting.Source
        public_base_url_source = $originSource
        public_base_url_redacted = if ($originResult -and $originResult.Ok) { $originResult.RedactedOrigin } else { "" }
        websocket_approvals_url_redacted = if ($originResult -and $originResult.Ok) { Format-RedactedWebSocketUrl $originResult.Origin "/ws/mobile/approvals" } else { "" }
        websocket_remote_screen_url_redacted = if ($originResult -and $originResult.Ok) { Format-RedactedWebSocketUrl $originResult.Origin "/ws/remote/screen" } else { "" }
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
        cert_file_label = if (Test-Configured $certPath) { Redact-DisplayLabel (Split-Path -Leaf $certPath) } else { "" }
        key_file_label = if (Test-Configured $keyPath) { Redact-DisplayLabel (Split-Path -Leaf $keyPath) } else { "" }
        tls_material_validation_attempted = $tlsValidation.Attempted
        tls_material_valid = $tlsValidation.Ok
        tls_host_valid = $tlsValidation.HostOk
        certificate_fingerprint_sha256 = $tlsValidation.CertificateFingerprintSha256
    }
    qr_payload_shape = $qrShape
    issues = @($issues)
    warnings = @($warnings)
    redacted_evidence_checklist_path = $displayChecklistPath
    next_manual_evidence_needed = @(
        "Real Android device or documented emulator identity",
        "Real camera/QR scan path if scan-to-pair is claimed",
        "Actual HTTPS/WSS connection from that device",
        "Actual HTTPS pairing/API reachability from that device",
        "Actual approval WebSocket over WSS from that device",
        "Actual remote screen WebSocket over WSS from that device",
        "Actual remote input WebSocket over WSS from that device when input is in scope",
        "Certificate source, fingerprint or CA identity, and explicit Android/emulator trust path",
        "Remote input revoke and expiry evidence that returns the device to read-only/no-input state",
        "Approval/remote screen/remote input screenshots or logs with tokens, pairing codes, hostnames, private paths, and device names redacted unless explicitly local-only"
    )
    manual_real_device_evidence_template = $manualRealDeviceEvidenceTemplate
}

$summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $summaryPath -Encoding utf8

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# Mobile LAN/WSS Real-Device Evidence Checklist")
$markdownLines.Add("")
$markdownLines.Add("Generated UTC: " + [string]$summary.generated_at_utc)
$markdownLines.Add("Preflight result: " + [string]$summary.result)
$markdownLines.Add("Preflight JSON: " + $displaySummaryPath)
$markdownLines.Add("")
$markdownLines.Add("This checklist is preflight/config evidence only. It is not real-device pass evidence, not RC sign-off, and not a substitute for reviewed phone/emulator artifacts.")
$markdownLines.Add("")
$markdownLines.Add("## Claim Controls")
$markdownLines.Add("")
$markdownLines.Add("- template_status: " + [string]$manualRealDeviceEvidenceTemplate["template_status"])
$markdownLines.Add("- real_device_result: " + [string]$manualRealDeviceEvidenceTemplate["real_device_result"])
$markdownLines.Add("- real_device_evidence_status: " + [string]$manualRealDeviceEvidenceTemplate["real_device_evidence_status"])
$markdownLines.Add("- real_device_evidence_collected=false")
$markdownLines.Add("- no_phone_preflight_claim: " + [string]$manualRealDeviceEvidenceTemplate["no_phone_preflight_claim"])
$markdownLines.Add("- may_be_recorded_as: " + [string]$manualRealDeviceEvidenceTemplate["may_be_recorded_as"])
$markdownLines.Add("- must_not_be_recorded_as: " + [string]$manualRealDeviceEvidenceTemplate["must_not_be_recorded_as"])
$markdownLines.Add("- real_device_pass_claim_allowed=false")
$markdownLines.Add("- preflight_ready_is_pass=false")
$markdownLines.Add("")
$markdownLines.Add("## Beginner Collection Path")
$markdownLines.Add("")
$markdownLines.Add("Do not mark any item complete while using only this computer. A phone or emulator must perform the action named in each step.")
$markdownLines.Add("")
$markdownLines.Add("1. Keep this preflight output as prerequisite/config evidence.")
$markdownLines.Add("2. Put the target Android device or emulator on the same LAN path as the backend.")
$markdownLines.Add("3. Trust the certificate on that exact device profile and record the trust path.")
$markdownLines.Add("4. Scan the QR with the device camera when scan-to-pair is claimed; label pasted/manual pairing as fallback only.")
$markdownLines.Add("5. Collect approval WSS, remote screen WSS, and remote input WSS evidence as separate artifacts.")
$markdownLines.Add("6. Prove revoke and expiry stop remote input before any remote input pass claim.")
$markdownLines.Add("7. Review and redact every screenshot, video, log, and trace before sharing.")
$markdownLines.Add("")
$markdownLines.Add("## Redaction Rules")
$markdownLines.Add("")
$markdownLines.Add("- Shareable packet: " + [string]$manualRealDeviceEvidenceTemplate["artifact_collection_rules"]["shareable_packet"])
$markdownLines.Add("- Local-only raw values: " + [string]$manualRealDeviceEvidenceTemplate["artifact_collection_rules"]["local_only_raw_values"])
$markdownLines.Add("- Token-bearing URLs: " + [string]$manualRealDeviceEvidenceTemplate["artifact_collection_rules"]["token_bearing_urls"])
$markdownLines.Add("- Required redactions:")
foreach ($redaction in @($manualRealDeviceEvidenceTemplate["required_redactions"])) {
    $markdownLines.Add("  - [ ] " + [string]$redaction)
}
$markdownLines.Add("")
$markdownLines.Add("## Operator Order")
$markdownLines.Add("")
foreach ($step in @($manualRealDeviceEvidenceTemplate["operator_collection_order"])) {
    $markdownLines.Add("- [ ] " + [string]$step)
}
$markdownLines.Add("")
$markdownLines.Add("## Fill Only After Reviewed Real-Device Evidence Exists")
$markdownLines.Add("")
$fields = $manualRealDeviceEvidenceTemplate["fields"]
$markdownLines.Add("- candidate: " + [string]$fields["candidate"])
$markdownLines.Add("- device_identity_redacted: " + [string]$fields["device_identity_redacted"])
$markdownLines.Add("- same_lan_path_redacted: " + [string]$fields["same_lan_path_redacted"])
$markdownLines.Add("- https_origin_redacted: " + [string]$fields["https_origin_redacted"])
$markdownLines.Add("- approval_wss_origin_redacted: " + [string]$fields["approval_wss_origin_redacted"])
$markdownLines.Add("- remote_screen_wss_origin_redacted: " + [string]$fields["remote_screen_wss_origin_redacted"])
$markdownLines.Add("- remote_input_wss_origin_redacted: " + [string]$fields["remote_input_wss_origin_redacted"])
$markdownLines.Add("- certificate_trust_path: " + [string]$fields["certificate_trust_path"])
$markdownLines.Add("- camera_qr_path_evidence: " + [string]$fields["camera_qr_path_evidence"])
$markdownLines.Add("- actual_device_https_wss_evidence: " + [string]$fields["actual_device_https_wss_evidence"])
$markdownLines.Add("- approval_wss_evidence: " + [string]$fields["approval_wss_evidence"])
$markdownLines.Add("- remote_screen_wss_evidence: " + [string]$fields["remote_screen_wss_evidence"])
$markdownLines.Add("- remote_input_wss_evidence: " + [string]$fields["remote_input_wss_evidence"])
$markdownLines.Add("- certificate_trust_evidence: " + [string]$fields["certificate_trust_evidence"])
$markdownLines.Add("- remote_input_grant_revoke_evidence: " + [string]$fields["remote_input_grant_revoke_evidence"])
$markdownLines.Add("- remote_input_grant_expiry_evidence: " + [string]$fields["remote_input_grant_expiry_evidence"])
$markdownLines.Add("- grant_revoke_expiry_artifact_review: " + [string]$fields["grant_revoke_expiry_artifact_review"])
$markdownLines.Add("- artifact_redaction_review: " + [string]$fields["artifact_redaction_review"])
$markdownLines.Add("")
$checklist = $manualRealDeviceEvidenceTemplate["real_device_collection_checklist"]
Add-MarkdownChecklistBlock $markdownLines "Camera QR" $checklist["camera_qr"]
Add-MarkdownChecklistBlock $markdownLines "Actual HTTPS/WSS" $checklist["actual_https_wss"]
Add-MarkdownChecklistBlock $markdownLines "Approval WSS" $checklist["approval_wss"]
Add-MarkdownChecklistBlock $markdownLines "Remote Screen WSS" $checklist["remote_screen_wss"]
Add-MarkdownChecklistBlock $markdownLines "Remote Input WSS" $checklist["remote_input_wss"]
Add-MarkdownChecklistBlock $markdownLines "Certificate Trust" $checklist["certificate_trust"]
Add-MarkdownChecklistBlock $markdownLines "Remote Input Grant Revoke/Expiry" $checklist["remote_input_grant_revoke_expiry"]
Add-MarkdownChecklistBlock $markdownLines "Screenshot/Log Review" $checklist["screenshot_log_review"]
if ($issues.Count -gt 0) {
    $markdownLines.Add("## Blocked Reasons Redacted")
    $markdownLines.Add("")
    foreach ($issue in @($issues)) {
        $markdownLines.Add("- " + [string]$issue)
    }
    $markdownLines.Add("")
}
$markdownLines | Set-Content -LiteralPath $checklistPath -Encoding utf8

Write-Host "Mobile LAN/WSS prerequisite preflight"
Write-Host $HttpsWssRequirement
Write-Host $NonEvidenceWarning
Write-Host $ManualEvidenceChecklistWarning
Write-Host "Fail-closed real-device status: $FailClosedRealDeviceStatus; no phone/emulator evidence has been collected by this script."
Write-Host "Redacted evidence summary path: $displaySummaryPath"
Write-Host "Redacted real-device checklist path: $displayChecklistPath"

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
    Write-Host " - Keep manual_real_device_evidence_template.real_device_result=uncollected while blocked." -ForegroundColor Yellow
    Write-Host " - Restart the backend after changing TLS env/config, rerun this preflight, then attach separate redacted phone/emulator evidence for camera QR, HTTPS/WSS, certificate trust, grant revoke/expiry, and screenshot/log review." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "[ready] LAN/WSS prerequisites are ready for manual Android/emulator evidence collection." -ForegroundColor Green
Write-Host "This is still not a real-device pass; record it only as prereq/config evidence." -ForegroundColor Yellow
Write-Host "Next manual checklist: camera QR, actual device HTTPS/WSS, certificate trust, grant revoke/expiry, and screenshot/log review." -ForegroundColor Yellow
exit 0
