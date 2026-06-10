[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$MobilePreflightEvidenceRoot = "",
    [string]$AndroidRealDeviceEvidenceRoot = "",
    [string]$AndroidReleaseGateEvidenceRoot = "",
    [string]$QaEvidenceRoot = "",
    [string]$DiagnosticsReviewEvidenceRoot = "",
    [string]$ResultQualityReviewEvidenceRoot = "",
    [string]$LocalModelCleanMachineEvidenceRoot = "",
    [string]$RcHandoffEvidenceRoot = "",
    [string]$PortableFirstScreenEvidenceRoot = ""
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

function Resolve-InputPath([string]$PathValue, [string]$DefaultRelativePath) {
    $value = if ([string]::IsNullOrWhiteSpace($PathValue)) {
        Join-Path $resolvedRoot $DefaultRelativePath
    }
    elseif ([System.IO.Path]::IsPathRooted($PathValue)) {
        $PathValue
    }
    else {
        Join-Path $resolvedRoot $PathValue
    }

    return [System.IO.Path]::GetFullPath($value)
}

function Resolve-IsolatedOptionalInputPath([string]$PathValue, [string]$DefaultRelativePath, [string]$IsolatedLeafName) {
    if (-not [string]::IsNullOrWhiteSpace($PathValue)) {
        return Resolve-InputPath $PathValue $DefaultRelativePath
    }

    if (-not [string]::IsNullOrWhiteSpace($EvidenceRoot)) {
        $rawEvidenceRoot = if ([System.IO.Path]::IsPathRooted($EvidenceRoot)) {
            $EvidenceRoot
        }
        else {
            Join-Path $resolvedRoot $EvidenceRoot
        }
        $fullEvidenceRoot = [System.IO.Path]::GetFullPath($rawEvidenceRoot)
        $rootPrefix = $resolvedRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
        if (-not $fullEvidenceRoot.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $fullEvidenceRoot) $IsolatedLeafName))
        }
    }

    return Resolve-InputPath "" $DefaultRelativePath
}

function Get-DisplayPath([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return ""
    }

    try {
        $fullPath = [System.IO.Path]::GetFullPath($PathValue)
    }
    catch {
        return (Redact-DisplayLabel (Split-Path -Leaf $PathValue))
    }

    $rootPrefix = $resolvedRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
    if ($fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return (Redact-DisplayLabel ($fullPath.Substring($rootPrefix.Length)))
    }

    return (Redact-DisplayLabel (Split-Path -Leaf $fullPath))
}

function Redact-DisplayLabel([string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Label)) {
        return ""
    }

    $text = $Label.Trim()
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{4,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "(?i)\b(?:contoso|acme|customer)[A-Za-z0-9._-]*", "[redacted-org]")
    $text = [regex]::Replace($text, "(?i)([?&](?:token|api[_-]?key|client_secret|secret|password|code)=)[^&\s]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)([?&](?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=)[^&\s]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)\bhttps?://[^/\s\\]+", "https://[redacted-host]")
    $text = [regex]::Replace($text, "(?i)\bwss?://[^/\s\\]+", "wss://[redacted-host]")
    $text = [regex]::Replace($text, "\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-host]")
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)=[A-Za-z0-9._~+/=-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=[A-Za-z0-9._~+/=-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
    return $text
}

function Redact-TextValue([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    $text = $Value.Trim()

    try {
        if ([System.IO.Path]::IsPathRooted($text)) {
            return Get-DisplayPath $text
        }
    }
    catch {
    }

    try {
        $uri = [Uri]$text
        if ($uri.IsAbsoluteUri -and $uri.Scheme -in @("http", "https", "ws", "wss")) {
            $port = if ($uri.IsDefaultPort) { "" } else { ":$($uri.Port)" }
            $path = if ([string]::IsNullOrWhiteSpace($uri.AbsolutePath) -or $uri.AbsolutePath -eq "/") { "" } else { "/[redacted-path]" }
            return "$($uri.Scheme)://[redacted-host]$port$path"
        }
    }
    catch {
    }

    $text = [regex]::Replace($text, "(?i)(authorization:\s*bearer\s+)[^\s,;]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)\b(set-cookie|cookie)\s*[:=]\s*[^,\r\n]+", '${1}: [redacted]')
    $text = [regex]::Replace($text, "(?i)\b(session|cookie|token|api[_-]?key|client_secret|secret|password|code|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=([^&\s,;]+)", '${1}=[redacted]')
    $text = [regex]::Replace($text, "(?i)\b(pairing\s+code|one[-\s]?time\s+(?:code|passcode|password)|otp)\s*[:=]?\s+[A-Za-z0-9._-]{4,}", '${1} [redacted]')
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{8,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "[A-Za-z]:\\[^\s,;]+", "[redacted-path]")
    $text = [regex]::Replace($text, "(?<!\w)/(?:Users|home)/[^\s,;]+", "[redacted-path]")
    return (Redact-DisplayLabel $text)
}

function Read-WorkspaceText([string]$RelativePath) {
    $path = Join-Path $resolvedRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        return [pscustomobject]@{
            relative_path = $RelativePath
            exists = $false
            text = ""
        }
    }

    return [pscustomobject]@{
        relative_path = $RelativePath
        exists = $true
        text = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    }
}

function Get-MissingNeedles([string]$Text, [string[]]$Needles) {
    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($needle in $Needles) {
        if ($Text.IndexOf($needle, [System.StringComparison]::Ordinal) -lt 0) {
            $missing.Add($needle)
        }
    }
    return @($missing)
}

function Count-TestContracts([string]$RelativePath) {
    $file = Read-WorkspaceText $RelativePath
    $count = 0
    if ($file.exists) {
        $count = [regex]::Matches(
            $file.text,
            "^(?:async\s+def|def)\s+test_",
            [System.Text.RegularExpressions.RegexOptions]::Multiline
        ).Count
    }

    return [ordered]@{
        path = $RelativePath
        exists = $file.exists
        test_contract_count = $count
    }
}

function Get-ArrayCount($Value) {
    if ($null -eq $Value) {
        return 0
    }
    if ($Value -is [array]) {
        return $Value.Count
    }
    return @($Value).Count
}

function Test-JsonFalse($Value) {
    return ($Value -is [bool]) -and ($Value -eq $false)
}

function Test-JsonTrue($Value) {
    return ($Value -is [bool]) -and ($Value -eq $true)
}

function Test-JsonBool($Value) {
    return ($Value -is [bool])
}

function Get-StrictJsonBoolValue($Value) {
    return (Test-JsonTrue $Value)
}

function Test-JsonIntegerOne($Value) {
    return (($Value -is [int]) -or ($Value -is [long])) -and ([int64]$Value -eq 1)
}

function Test-JsonNonNegativeInteger($Value) {
    return (($Value -is [int]) -or ($Value -is [long])) -and ([int64]$Value -ge 0)
}

function Get-StrictJsonNonNegativeIntegerOrZero($Value) {
    if (Test-JsonNonNegativeInteger $Value) {
        return [int64]$Value
    }
    return 0
}

function Test-UtcTimestampValue([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }

    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse($Value, [ref]$parsed)) {
        return $false
    }

    return $Value.TrimEnd().EndsWith("Z", [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-EmptyArrayValue($Value) {
    return (Get-ArrayCount $Value) -eq 0
}

function Test-MobileRedactedHostLabel([string]$Value) {
    return $Value -in @("[redacted-host]", "[loopback]", "[bind-address]")
}

function Test-MobileRedactedHttpOrigin([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    return $Value -match "^https?://\[(?:redacted-host|loopback|bind-address)\](?::\d{1,5})?$"
}

function Test-MobileRedactedWebSocketUrl([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    return $Value -match "^wss?://\[(?:redacted-host|loopback|bind-address)\](?::\d{1,5})?/ws/(?:mobile/approvals|remote/screen|remote/input)$"
}

function Test-MobileRedactedWebSocketPath([string]$Value, [string]$ExpectedPath) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    $escapedPath = [regex]::Escape($ExpectedPath)
    return $Value -match "^wss?://\[(?:redacted-host|loopback|bind-address)\](?::\d{1,5})?$escapedPath$"
}

function Get-SafeMobileHostLabel([string]$Value) {
    if (Test-MobileRedactedHostLabel $Value) {
        return $Value
    }
    return "invalid_redacted"
}

function Get-SafeMobileHttpOrigin([string]$Value) {
    if (Test-MobileRedactedHttpOrigin $Value) {
        return $Value
    }
    return "invalid_redacted"
}

function Get-SafeMobileWebSocketUrl([string]$Value) {
    if (Test-MobileRedactedWebSocketUrl $Value) {
        return $Value
    }
    return "invalid_redacted"
}

function Test-ArrayContainsText($Value, [string]$Needle) {
    foreach ($item in @($Value)) {
        if ([string]$item -eq $Needle) {
            return $true
        }
    }
    return $false
}

function Test-MeaningfulEvidenceValue([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }

    $text = $Value.Trim()
    $lower = $text.ToLowerInvariant()
    if ($text -match "<[^>]+>") {
        return $false
    }
    if ($lower -in @("todo", "to do", "tbd", "pending", "unknown", "fixme", "placeholder", "uncollected", "blocked")) {
        return $false
    }
    if ($lower -match "^(?:todo|to do|tbd|pending|unknown|fixme|placeholder)(?:$|[\s:._-])") {
        return $false
    }
    return $true
}

function Test-RcGateExitValue([string]$Value) {
    if (-not (Test-MeaningfulEvidenceValue $Value)) {
        return $false
    }
    return $Value.Trim().ToLowerInvariant() -match "(exit|code|status|pass|fail|success|error|blocked|\b0\b|\b1\b)"
}

function Test-LocalModelArtifactBuildProfileStatus([string]$Value) {
    return $Value -in @("recorded_unverified_by_this_helper", "missing_required_fields")
}

function Get-SafeLocalModelArtifactBuildProfileStatus([string]$Value) {
    if (Test-LocalModelArtifactBuildProfileStatus $Value) {
        return $Value
    }
    return "invalid_redacted"
}

function Test-LocalModelStepStatus([string]$Value) {
    return $Value -in @(
        "manual_outcome_recorded_unverified_by_this_helper",
        "blocked_reason_recorded",
        "blocked_by_overall_reason_recorded",
        "blocked_missing_outcome_or_blocked_reason"
    )
}

function Get-SafeLocalModelStepStatus([string]$Value) {
    if (Test-LocalModelStepStatus $Value) {
        return $Value
    }
    return "invalid_redacted"
}

function New-SafeLocalModelStepSummary($Step) {
    if ($null -eq $Step) {
        return [ordered]@{
            outcome = "invalid_redacted"
            status = "invalid_redacted"
            blocked_reason_count = 0
            pass_verified_by_this_helper = $false
            clean_machine_pass = $false
        }
    }

    return [ordered]@{
        outcome = Redact-TextValue ([string]$Step.outcome)
        status = Get-SafeLocalModelStepStatus ([string]$Step.status)
        blocked_reason_count = Get-ArrayCount $Step.blocked_reason_redacted
        pass_verified_by_this_helper = $false
        clean_machine_pass = $false
    }
}

function Get-SourceContract([string]$RelativePath, [string[]]$Needles) {
    $file = Read-WorkspaceText $RelativePath
    $missing = if ($file.exists) { Get-MissingNeedles $file.text $Needles } else { @("file_missing") }
    return [ordered]@{
        path = $RelativePath
        exists = $file.exists
        required_markers_present = ($missing.Count -eq 0)
        missing_markers = @($missing)
    }
}

function Find-LatestJsonArtifact([string]$RootPath, [string]$FileName) {
    if (-not (Test-Path -LiteralPath $RootPath)) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            data = $null
            error = ""
        }
    }

    $files = @(
        Get-ChildItem -LiteralPath $RootPath -Recurse -Filter $FileName -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($files.Count -eq 0) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            data = $null
            error = ""
        }
    }

    $latest = $files[0]
    try {
        $data = Get-Content -LiteralPath $latest.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            data = $data
            error = ""
        }
    }
    catch {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            data = $null
            error = "latest JSON artifact could not be parsed"
        }
    }
}

function Find-LatestTextArtifact([string]$RootPath, [string]$FileName) {
    if (-not (Test-Path -LiteralPath $RootPath)) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            text = ""
            error = ""
        }
    }

    $files = @(
        Get-ChildItem -LiteralPath $RootPath -Recurse -Filter $FileName -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($files.Count -eq 0) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            text = ""
            error = ""
        }
    }

    $latest = $files[0]
    try {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            text = Get-Content -LiteralPath $latest.FullName -Raw -Encoding UTF8
            error = ""
        }
    }
    catch {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            text = ""
            error = "latest text artifact could not be read"
        }
    }
}

function Get-RegexFirstGroup([string]$Text, [string]$Pattern) {
    $match = [regex]::Match($Text, $Pattern)
    if ($match.Success -and $match.Groups.Count -gt 1) {
        return [string]$match.Groups[1].Value
    }
    return ""
}

function ConvertTo-IntOrZero([string]$Value) {
    $parsed = 0
    if ([int]::TryParse($Value, [ref]$parsed)) {
        return $parsed
    }
    return 0
}

function Get-FirstLogLine([string]$Text, [string]$Pattern) {
    foreach ($line in ($Text -split "\r?\n")) {
        if ($line -match $Pattern) {
            return [string]$line
        }
    }
    return ""
}

$evidenceRootPath = Resolve-InputPath $EvidenceRoot ".tmp\release-evidence-packet"
$mobileEvidenceRootPath = Resolve-InputPath $MobilePreflightEvidenceRoot ".tmp\mobile-lan-wss-preflight"
$androidRealDeviceEvidenceRootPath = Resolve-IsolatedOptionalInputPath $AndroidRealDeviceEvidenceRoot ".tmp\android-real-device-evidence-template" "empty-android-real-device-evidence-template"
$androidReleaseGateEvidenceRootPath = Resolve-IsolatedOptionalInputPath $AndroidReleaseGateEvidenceRoot ".tmp\android-release-gate" "empty-android-release-gate"
$qaEvidenceRootPath = Resolve-InputPath $QaEvidenceRoot ".tmp\qa-evidence"
$diagnosticsReviewEvidenceRootPath = Resolve-InputPath $DiagnosticsReviewEvidenceRoot ".tmp\diagnostics-external-review"
$resultQualityReviewEvidenceRootPath = Resolve-InputPath $ResultQualityReviewEvidenceRoot ".tmp\result-quality-review"
$localModelCleanMachineEvidenceRootPath = Resolve-InputPath $LocalModelCleanMachineEvidenceRoot ".tmp\local-model-clean-machine-evidence"
$rcHandoffEvidenceRootPath = Resolve-InputPath $RcHandoffEvidenceRoot ".tmp\rc-handoff-template"
$portableFirstScreenEvidenceRootPath = Resolve-InputPath $PortableFirstScreenEvidenceRoot ".tmp\portable-first-screen-smoke"
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $evidenceRootPath "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$jsonPath = Join-Path $runRoot "release-evidence-packet.redacted.json"
$markdownPath = Join-Path $runRoot "release-evidence-packet.redacted.md"

$contractFailures = New-Object System.Collections.Generic.List[string]

$mobileNeedles = @(
    "evidence-summary.redacted.json",
    "redacted_evidence_summary_path",
    "real_device_evidence_status",
    "uncollected_fail_closed",
    "real_device_evidence_collected",
    "no_phone_preflight_claim",
    "not_real_device_pass",
    "ready_for_manual_real_device_collection_only",
    "approval_wss_evidence",
    "remote_screen_wss_evidence",
    "remote_input_wss_evidence",
    "Token-bearing mobile LAN flows require HTTPS and WSS",
    "This preflight does not use a phone, emulator, camera, QR scanner, or real WSS connection",
    "must not be recorded as real-device pass evidence"
)
$mobileContract = Get-SourceContract "scripts/verify_mobile_lan_wss_preflight.ps1" $mobileNeedles
if (-not $mobileContract.required_markers_present) {
    $contractFailures.Add("mobile LAN/WSS preflight source contract is missing required redaction or non-evidence markers")
}

$androidReleaseGateNeedles = @(
    "android-release-gate.redacted.json",
    "preflight_ready_not_release",
    "Strict Android release gate requires -ArtifactPath",
    "Strict Android release gate requires -RealDeviceEvidencePath",
    "artifact_type=android-real-device-remote-control-evidence",
    "artifact_not_apk_zip",
    "installable_android_app_claim_allowed",
    "real_device_remote_control_claim_allowed",
    "expo_preview_is_not_release",
    "requires_reviewed_apk_install_evidence",
    "requires_reviewed_https_wss_remote_control_evidence",
    "This is not an installable APK pass or real-device remote-control pass."
)
$androidReleaseGateContract = Get-SourceContract "scripts/verify_android_release_gate.ps1" $androidReleaseGateNeedles
if (-not $androidReleaseGateContract.required_markers_present) {
    $contractFailures.Add("Android release gate source contract is missing required fail-closed markers")
}

$androidRealDeviceTemplateNeedles = @(
    "android-real-device-evidence.redacted.template.json",
    "manual_real_device_evidence_required",
    "real_device_pass_claim_allowed",
    "camera_qr_pairing",
    "https_api_reachability",
    "certificate_trust_path",
    "approval_wss",
    "remote_screen_wss",
    "remote_input_wss",
    "click_input_approval",
    "text_input_approval",
    "key_pagedown_approval",
    "mobile_end_control_readonly",
    "desktop_revoke_readonly",
    "grant_expiry_readonly",
    "build_environment",
    "local_apk_build_ready",
    "binding_ref",
    "raw_device_ids_absent",
    "raw_grant_ids_absent"
)
$androidRealDeviceTemplateContract = Get-SourceContract "scripts/collect_android_real_device_evidence_template.ps1" $androidRealDeviceTemplateNeedles
if (-not $androidRealDeviceTemplateContract.required_markers_present) {
    $contractFailures.Add("Android real-device evidence template source contract is missing required scenario or redaction markers")
}

$portableNeedles = @(
    "portable.status.log",
    "portable renderer DOM read-only task evidence passed",
    "portable renderer DOM natural-language read-only task evidence passed",
    "it is not accepted as natural-language task evidence",
    "This is submission/task-evidence coverage",
    "completed task-result sign-off",
    "Visible safe-failure copy is still useful safety evidence",
    "Any forbidden mutation or diagnostics export during this attempt fails the smoke"
)
$portableContract = Get-SourceContract "docs/qa/release-gate.md" $portableNeedles
if (-not $portableContract.required_markers_present) {
    $contractFailures.Add("portable first-screen smoke source contract is missing required non-signoff markers")
}

$mobileRemoteInputUiNeedles = @(
    "ApprovalActiveGrantContext",
    "REMOTE_INPUT_ACTIVE_GRANT_REASON",
    "remoteInputApprovalMatchesActiveGrant",
    "source_device_id",
    "source_grant_id",
    "required_mobile_scopes",
    "binding_ref"
)
$mobileRemoteInputClientNeedles = @(
    "assertRemoteInputApprovalMatchesSession",
    "assertRemoteInputApprovalRejectAllowedForSession",
    "Remote input approval does not match this mobile device.",
    "Remote input approval does not match the active mobile grant.",
    "getApprovalDetail(session, approvalId)",
    "remote_input_binding",
    "binding_ref",
    "allowed_device_ids",
    "claimRemoteInputGrantToken(session, explicitGrantId)"
)
$mobileRemoteInputSmokeNeedles = @(
    "remoteInputNoActiveGrant",
    "remoteInputWrongActiveGrant",
    "binding_ref",
    "matching approval details",
    "client-side remote-input binding failures must not reach the smoke server",
    "remote-input approval without a cached grant token must fail closed",
    "Approval stream connected snapshot must restore active remote-input grants after missed events"
)
$mobileRemoteInputContracts = @(
    Get-SourceContract "mobile/src/approvalSafetyDisplay.ts" $mobileRemoteInputUiNeedles
    Get-SourceContract "mobile/src/api/client.ts" $mobileRemoteInputClientNeedles
    Get-SourceContract "mobile/scripts/remote-input-grant-smoke.cjs" $mobileRemoteInputSmokeNeedles
)
foreach ($contract in $mobileRemoteInputContracts) {
    if (-not $contract.required_markers_present) {
        $contractFailures.Add("mobile remote-input active grant source contract is missing required fail-closed markers")
        break
    }
}
$latestPortableStatus = Find-LatestTextArtifact $portableFirstScreenEvidenceRootPath "portable.status.log"
$portableLatestSummary = if ($latestPortableStatus.found -and -not [string]::IsNullOrWhiteSpace($latestPortableStatus.text)) {
    $portableText = [string]$latestPortableStatus.text
    $readOnlyLine = Get-FirstLogLine $portableText "\[pass\]\s+portable renderer DOM read-only task evidence passed:"
    $naturalLanguageLine = Get-FirstLogLine $portableText "\[pass\]\s+portable renderer DOM natural-language read-only task evidence passed:"
    $firstScreenLine = Get-FirstLogLine $portableText "\[pass\]\s+portable first-screen/read-only diagnostics smoke passed:"
    $readOnlyPass = -not [string]::IsNullOrWhiteSpace($readOnlyLine)
    $naturalLanguagePass = -not [string]::IsNullOrWhiteSpace($naturalLanguageLine)
    $firstScreenPass = -not [string]::IsNullOrWhiteSpace($firstScreenLine)
    $unsupported = $portableText -match "\[unsupported\]"
    $failed = $portableText -match "\[(fail|blocked)\]"
    $readOnlyTasksValue = Get-RegexFirstGroup $readOnlyLine "(?<![A-Za-z0-9_])tasks[=:]\s*(\d+)"
    $readOnlyRunsValue = Get-RegexFirstGroup $readOnlyLine "(?<![A-Za-z0-9_])runs[=:]\s*(\d+)"
    $readOnlyChatValue = Get-RegexFirstGroup $readOnlyLine "chat messages[=:]\s*(\d+)"
    $readOnlyDiagnosticPackagesValue = Get-RegexFirstGroup $readOnlyLine "diagnostic-packages[=:]\s*(\d+)"
    $naturalLanguageRunsValue = Get-RegexFirstGroup $naturalLanguageLine "(?<![A-Za-z0-9_])runs[=:]\s*(\d+)"
    $naturalLanguageTasksValue = Get-RegexFirstGroup $naturalLanguageLine "(?<![A-Za-z0-9_])tasks[=:]\s*(\d+)"
    $naturalLanguageRelatedTasksValue = Get-RegexFirstGroup $naturalLanguageLine "relatedTasks[=:]\s*(\d+)"
    $naturalLanguageRelatedRunsValue = Get-RegexFirstGroup $naturalLanguageLine "relatedRuns[=:]\s*(\d+)"
    $naturalLanguageChatValue = Get-RegexFirstGroup $naturalLanguageLine "chat messages[=:]\s*(\d+)"
    $naturalLanguageDiagnosticPackagesValue = Get-RegexFirstGroup $naturalLanguageLine "diagnostic-packages[=:]\s*(\d+)"
    $naturalLanguageCompletionLevelValue = Get-RegexFirstGroup $naturalLanguageLine "(?i)completion_evidence\.level[=:]\s*([A-Za-z0-9_\-]+)"
    $naturalLanguageResultVerifiedValue = Get-RegexFirstGroup $naturalLanguageLine "(?i)result_verified[=:]\s*(true|false)"
    $naturalLanguageSignoffValue = Get-RegexFirstGroup $naturalLanguageLine "(?i)(?<![A-Za-z0-9_])signoff[=:]\s*(true|false)"
    $readOnlyTasks = ConvertTo-IntOrZero $readOnlyTasksValue
    $readOnlyRuns = ConvertTo-IntOrZero $readOnlyRunsValue
    $readOnlyChatMessages = ConvertTo-IntOrZero $readOnlyChatValue
    $readOnlyDiagnosticPackages = ConvertTo-IntOrZero $readOnlyDiagnosticPackagesValue
    $naturalLanguageTasks = ConvertTo-IntOrZero $naturalLanguageTasksValue
    $naturalLanguageRuns = ConvertTo-IntOrZero $naturalLanguageRunsValue
    $naturalLanguageRelatedTasks = ConvertTo-IntOrZero $naturalLanguageRelatedTasksValue
    $naturalLanguageRelatedRuns = ConvertTo-IntOrZero $naturalLanguageRelatedRunsValue
    $naturalLanguageChatMessages = ConvertTo-IntOrZero $naturalLanguageChatValue
    $naturalLanguageDiagnosticPackages = ConvertTo-IntOrZero $naturalLanguageDiagnosticPackagesValue
    $allowedCompletionLevels = @("submission", "task_created", "visible_progress", "completed_result", "safe_failure", "not_collected")
    $naturalLanguageCompletionLevel = if ([string]::IsNullOrWhiteSpace($naturalLanguageCompletionLevelValue)) {
        "not_collected"
    }
    elseif ($naturalLanguageCompletionLevelValue -in $allowedCompletionLevels) {
        $naturalLanguageCompletionLevelValue
    }
    else {
        "invalid"
    }
    $naturalLanguageResultVerified = $naturalLanguageResultVerifiedValue -match "^(?i:true)$"
    $naturalLanguageSignoff = $naturalLanguageSignoffValue -match "^(?i:true)$"
    $postEndpoint = if ($naturalLanguageLine -match "observed expected POST /api/runs") { "/api/runs" } elseif ($naturalLanguageLine -match "observed expected POST /api/chat") { "/api/chat" } else { "" }
    $readOnlyNoWrites = $readOnlyPass -and $readOnlyTasksValue -ne "" -and $readOnlyRunsValue -ne "" -and $readOnlyChatValue -ne "" -and $readOnlyDiagnosticPackagesValue -ne "" -and $readOnlyTasks -eq 0 -and $readOnlyRuns -eq 0 -and $readOnlyChatMessages -eq 0 -and $readOnlyDiagnosticPackages -eq 0
    $naturalLanguageSubmissionSemanticValid = $naturalLanguageLine -match "submitted natural-language prompt through packaged command dock"
    $naturalLanguageTaskSemanticValid = $naturalLanguageLine -match "natural-language prompt created read-only/system diagnostics task"
    $naturalLanguageRunSemanticValid = $naturalLanguageLine -match "natural-language prompt created read-only/system diagnostics run"
    $naturalLanguageSemanticValid = $naturalLanguageTaskSemanticValid -or $naturalLanguageRunSemanticValid
    $naturalLanguageCoreCountsPresent = $naturalLanguageTasksValue -ne "" -and $naturalLanguageRelatedTasksValue -ne "" -and $naturalLanguageRunsValue -ne "" -and $naturalLanguageRelatedRunsValue -ne ""
    $naturalLanguageTaskCountValid = $naturalLanguageTaskSemanticValid -and $naturalLanguageTasksValue -ne "" -and $naturalLanguageTasks -ge 1 -and $naturalLanguageRelatedTasksValue -ne "" -and $naturalLanguageRelatedTasks -ge 1
    $naturalLanguageRunCountValid = $naturalLanguageRunSemanticValid -and $naturalLanguageRunsValue -ne "" -and $naturalLanguageRuns -ge 1 -and $naturalLanguageRelatedRunsValue -ne "" -and $naturalLanguageRelatedRuns -ge 1
    $naturalLanguageRelatedEvidenceValid = $naturalLanguageTaskCountValid -or $naturalLanguageRunCountValid
    $naturalLanguageCountsValid = $naturalLanguagePass -and $naturalLanguageSubmissionSemanticValid -and $postEndpoint -ne "" -and $naturalLanguageCoreCountsPresent -and $naturalLanguageRelatedEvidenceValid -and $naturalLanguageChatValue -ne "" -and $naturalLanguageDiagnosticPackagesValue -ne "" -and $naturalLanguageChatMessages -eq 0 -and $naturalLanguageDiagnosticPackages -eq 0
    $naturalLanguageCompletedResultEvidenceCandidate = [bool]($naturalLanguageCountsValid -and $naturalLanguageCompletionLevel -eq "completed_result" -and $naturalLanguageResultVerified -and -not $naturalLanguageSignoff)
    $portableLogMismatches = New-Object System.Collections.Generic.List[string]
    if (($readOnlyPass -or $naturalLanguagePass -or $firstScreenPass) -and $failed) {
        $portableLogMismatches.Add("portable status log contains pass and fail/blocked lines")
    }
    if ($readOnlyPass -and -not $readOnlyNoWrites) {
        $portableLogMismatches.Add("read-only pass line does not prove zero task/run/chat/export side effects")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageCountsValid) {
        $portableLogMismatches.Add("natural-language pass line does not prove POST plus read-only task/run evidence")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageSubmissionSemanticValid) {
        $portableLogMismatches.Add("natural-language pass line does not prove packaged command-dock submission semantics")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageSemanticValid) {
        $portableLogMismatches.Add("natural-language pass line does not prove read-only/system diagnostics task or run semantics")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageCoreCountsPresent) {
        $portableLogMismatches.Add("natural-language pass line is missing required task/run relation counts")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageRelatedEvidenceValid) {
        $portableLogMismatches.Add("natural-language pass line does not prove related read-only/system diagnostics task or run evidence")
    }
    if ($naturalLanguagePass -and $naturalLanguageCompletionLevel -eq "invalid") {
        $portableLogMismatches.Add("natural-language pass line has invalid completion_evidence.level")
    }
    if ($naturalLanguagePass -and $naturalLanguageResultVerified -and $naturalLanguageCompletionLevel -ne "completed_result") {
        $portableLogMismatches.Add("natural-language pass line reports result_verified without completed_result level")
    }
    if ($naturalLanguagePass -and $naturalLanguageSignoff) {
        $portableLogMismatches.Add("natural-language pass line must not report completion_evidence signoff")
    }
    if (($readOnlyPass -or $naturalLanguagePass) -and -not $firstScreenPass) {
        $portableLogMismatches.Add("portable status log is missing final first-screen pass line")
    }
    if ($portableLogMismatches.Count -gt 0) {
        $contractFailures.Add("latest portable first-screen status log failed limited-evidence validation")
    }
    $naturalLanguageCompletedResultEvidence = [bool]($naturalLanguageCompletedResultEvidenceCandidate -and $portableLogMismatches.Count -eq 0)
    [ordered]@{
        found = $true
        path = $latestPortableStatus.path
        last_write_utc = $latestPortableStatus.last_write_utc
        source_contract_status = if ($portableLogMismatches.Count -gt 0) { "source_contract_mismatch" } elseif ($readOnlyPass -and $firstScreenPass -and $readOnlyNoWrites) { "valid_limited_evidence_log" } else { "limited_or_incomplete_evidence_log" }
        mismatch_reasons = @($portableLogMismatches)
        first_screen_read_only_pass = [bool]($readOnlyPass -and $firstScreenPass -and $readOnlyNoWrites -and $portableLogMismatches.Count -eq 0)
        renderer_dom_read_only_evidence = if ($readOnlyPass) { "passed" } elseif ($unsupported) { "unsupported" } else { "not_observed" }
        natural_language_submission_evidence = if ($naturalLanguagePass -and $naturalLanguageCountsValid -and $portableLogMismatches.Count -eq 0) { "packaged_command_dock_submission_plus_read_only_task_evidence" } elseif ($naturalLanguagePass) { "source_contract_mismatch" } elseif ($unsupported) { "unsupported" } else { "not_observed" }
        observed_post_endpoint = $postEndpoint
        task_evidence_kind = if ($naturalLanguagePass -and $naturalLanguageCountsValid -and $portableLogMismatches.Count -eq 0) { "read_only_system_diagnostics_task_or_run" } else { "not_observed" }
        read_only_counts = [ordered]@{
            tasks = $readOnlyTasks
            runs = $readOnlyRuns
            chat_messages = $readOnlyChatMessages
            diagnostic_packages = $readOnlyDiagnosticPackages
        }
        natural_language_counts = [ordered]@{
            tasks = $naturalLanguageTasks
            related_tasks = $naturalLanguageRelatedTasks
            runs = $naturalLanguageRuns
            related_runs = $naturalLanguageRelatedRuns
            chat_messages = $naturalLanguageChatMessages
            diagnostic_packages = $naturalLanguageDiagnosticPackages
        }
        natural_language_completion_evidence = [ordered]@{
            level = $naturalLanguageCompletionLevel
            result_verified = [bool]$naturalLanguageResultVerified
            completed_result_evidence = [bool]$naturalLanguageCompletedResultEvidence
            signoff = $false
        }
        unsupported_or_failed = [bool]($unsupported -or $failed)
        clean_machine_signoff = $false
        completed_task_result_signoff = $false
        release_candidate_signoff = $false
        not_signoff_reason = "packaged first-screen/read-only and natural-language submission evidence only"
    }
}
elseif ($latestPortableStatus.found) {
    $contractFailures.Add("latest portable first-screen status log could not be read or was empty")
    [ordered]@{
        found = $true
        path = $latestPortableStatus.path
        last_write_utc = $latestPortableStatus.last_write_utc
        source_contract_status = "source_contract_mismatch"
        parse_error = $latestPortableStatus.error
        first_screen_read_only_pass = $false
        natural_language_submission_evidence = "not_observed"
        natural_language_completion_evidence = [ordered]@{
            level = "not_collected"
            result_verified = $false
            completed_result_evidence = $false
            signoff = $false
        }
        clean_machine_signoff = $false
        completed_task_result_signoff = $false
        release_candidate_signoff = $false
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $portableFirstScreenEvidenceRootPath
        source_contract_status = "not_collected_by_this_packet"
        first_screen_read_only_pass = $false
        natural_language_submission_evidence = "not_observed"
        natural_language_completion_evidence = [ordered]@{
            level = "not_collected"
            result_verified = $false
            completed_result_evidence = $false
            signoff = $false
        }
        clean_machine_signoff = $false
        completed_task_result_signoff = $false
        release_candidate_signoff = $false
    }
}

$latestMobile = Find-LatestJsonArtifact $mobileEvidenceRootPath "evidence-summary.redacted.json"
$mobileLatestSummary = if ($latestMobile.found -and $null -ne $latestMobile.data) {
    $mobileArtifactMismatches = New-Object System.Collections.Generic.List[string]
    $mobileResult = [string]$latestMobile.data.result
    $mobileGeneratedAt = [string]$latestMobile.data.generated_at_utc
    $mobileGeneratedAtParsed = [DateTimeOffset]::MinValue
    $mobileHostRedacted = [string]$latestMobile.data.backend.host_redacted
    $mobilePublicBaseUrlRedacted = [string]$latestMobile.data.backend.public_base_url_redacted
    $mobileApprovalsUrlRedacted = [string]$latestMobile.data.backend.websocket_approvals_url_redacted
    $mobileRemoteScreenUrlRedacted = [string]$latestMobile.data.backend.websocket_remote_screen_url_redacted
    $mobileRemoteInputUrlRedacted = [string]$latestMobile.data.backend.websocket_remote_input_url_redacted
    $mobileQrApprovalsUrlRedacted = [string]$latestMobile.data.qr_payload_shape.websocket_approvals_url_redacted
    $mobileQrRemoteScreenUrlRedacted = [string]$latestMobile.data.qr_payload_shape.websocket_remote_screen_url_redacted
    $mobileQrRemoteInputUrlRedacted = [string]$latestMobile.data.qr_payload_shape.websocket_remote_input_url_redacted
    $mobileRealDeviceEvidenceStatus = [string]$latestMobile.data.real_device_evidence_status
    $mobileNoPhoneClaim = [string]$latestMobile.data.no_phone_preflight_claim
    $mobileTransportSecurityStatus = [string]$latestMobile.data.qr_payload_shape.transport_security_status
    $allowedMobileResults = @("ready_for_manual_real_device_collection_only", "blocked")
    $mobileReadyStatus = $mobileResult -eq "ready_for_manual_real_device_collection_only"
    $mobileChecklistNames = @(
        "camera_qr",
        "actual_https_wss",
        "approval_wss",
        "remote_screen_wss",
        "remote_input_wss",
        "certificate_trust",
        "remote_input_grant_revoke_expiry",
        "screenshot_log_review"
    )
    $mobileCollectionChecklistStatuses = [ordered]@{}
    if ($mobileResult -notin $allowedMobileResults) {
        $mobileArtifactMismatches.Add("result is not an allowed mobile preflight status")
    }
    if (-not [DateTimeOffset]::TryParse($mobileGeneratedAt, [ref]$mobileGeneratedAtParsed)) {
        $mobileArtifactMismatches.Add("generated_at_utc is not a timestamp")
    }
    if (-not (Test-MobileRedactedHostLabel $mobileHostRedacted)) {
        $mobileArtifactMismatches.Add("backend.host_redacted is not a safe redacted host label")
    }
    if (-not (Test-MobileRedactedHttpOrigin $mobilePublicBaseUrlRedacted)) {
        $mobileArtifactMismatches.Add("backend.public_base_url_redacted is not a safe redacted origin")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobilePublicBaseUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.public_base_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileApprovalsUrlRedacted "/ws/mobile/approvals")) {
        $mobileArtifactMismatches.Add("backend.websocket_approvals_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileApprovalsUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.websocket_approvals_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileRemoteScreenUrlRedacted "/ws/remote/screen")) {
        $mobileArtifactMismatches.Add("backend.websocket_remote_screen_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileRemoteScreenUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.websocket_remote_screen_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileRemoteInputUrlRedacted "/ws/remote/input")) {
        $mobileArtifactMismatches.Add("backend.websocket_remote_input_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileRemoteInputUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.websocket_remote_input_url_redacted")
    }
    if (-not (Test-JsonBool $latestMobile.data.lan_tls.enabled)) {
        $mobileArtifactMismatches.Add("lan_tls.enabled is not a JSON boolean")
    }
    if (-not (Test-JsonBool $latestMobile.data.lan_tls.tls_material_valid)) {
        $mobileArtifactMismatches.Add("lan_tls.tls_material_valid is not a JSON boolean")
    }
    if (-not (Test-JsonBool $latestMobile.data.lan_tls.tls_host_valid)) {
        $mobileArtifactMismatches.Add("lan_tls.tls_host_valid is not a JSON boolean")
    }
    if ($mobileTransportSecurityStatus -notin @("https_ready_preflight", "https_wss_preflight_blocked")) {
        $mobileArtifactMismatches.Add("qr_payload_shape.transport_security_status is not an allowed status")
    }
    if (-not (Test-JsonBool $latestMobile.data.qr_payload_shape.transport_security_tls_ready)) {
        $mobileArtifactMismatches.Add("qr_payload_shape.transport_security_tls_ready is not a JSON boolean")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileQrApprovalsUrlRedacted "/ws/mobile/approvals")) {
        $mobileArtifactMismatches.Add("qr_payload_shape.websocket_approvals_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrApprovalsUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing qr_payload_shape.websocket_approvals_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileQrRemoteScreenUrlRedacted "/ws/remote/screen")) {
        $mobileArtifactMismatches.Add("qr_payload_shape.websocket_remote_screen_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrRemoteScreenUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing qr_payload_shape.websocket_remote_screen_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileQrRemoteInputUrlRedacted "/ws/remote/input")) {
        $mobileArtifactMismatches.Add("qr_payload_shape.websocket_remote_input_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrRemoteInputUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing qr_payload_shape.websocket_remote_input_url_redacted")
    }
    if ($mobileRealDeviceEvidenceStatus -ne "uncollected_fail_closed") {
        $mobileArtifactMismatches.Add("real_device_evidence_status is not uncollected_fail_closed")
    }
    if (-not (Test-JsonFalse $latestMobile.data.real_device_evidence_collected)) {
        $mobileArtifactMismatches.Add("real_device_evidence_collected is not false")
    }
    if ($mobileNoPhoneClaim -ne "not_real_device_pass") {
        $mobileArtifactMismatches.Add("no_phone_preflight_claim is not not_real_device_pass")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.template_status -ne "manual_real_device_evidence_required") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.template_status is not manual_real_device_evidence_required")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.real_device_result -ne "uncollected") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_result is not uncollected")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.real_device_evidence_status -ne "uncollected_fail_closed") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_evidence_status is not uncollected_fail_closed")
    }
    if (-not (Test-JsonFalse $latestMobile.data.manual_real_device_evidence_template.real_device_evidence_collected)) {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_evidence_collected is not false")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.no_phone_preflight_claim -ne "not_real_device_pass") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.no_phone_preflight_claim is not not_real_device_pass")
    }
    if (-not (Test-JsonFalse $latestMobile.data.manual_real_device_evidence_template.claim_controls.real_device_pass_claim_allowed)) {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.claim_controls.real_device_pass_claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestMobile.data.manual_real_device_evidence_template.claim_controls.preflight_ready_is_pass)) {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.claim_controls.preflight_ready_is_pass is not false")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.may_be_recorded_as -ne "preflight/config evidence only") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.may_be_recorded_as is not preflight/config evidence only")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.must_not_be_recorded_as -ne "real-device pass evidence") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.must_not_be_recorded_as is not real-device pass evidence")
    }
    if (-not (Test-JsonTrue $latestMobile.data.manual_real_device_evidence_template.artifact_collection_rules.review_required_before_pass_claim)) {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.artifact_collection_rules.review_required_before_pass_claim is not true")
    }
    foreach ($fieldName in @(
        "camera_qr_path_evidence",
        "actual_device_https_wss_evidence",
        "approval_wss_evidence",
        "approval_artifact_review",
        "remote_screen_wss_evidence",
        "remote_screen_artifact_review",
        "remote_input_wss_evidence",
        "remote_input_artifact_review",
        "certificate_trust_evidence",
        "remote_input_grant_revoke_evidence",
        "remote_input_grant_expiry_evidence",
        "grant_revoke_expiry_artifact_review",
        "artifact_redaction_review"
    )) {
        if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.$fieldName -ne "uncollected") {
            $mobileArtifactMismatches.Add("manual_real_device_evidence_template.fields.$fieldName is not uncollected")
        }
    }
    foreach ($checklistName in $mobileChecklistNames) {
        $checklistEntry = $latestMobile.data.manual_real_device_evidence_template.real_device_collection_checklist.$checklistName
        $checklistStatus = [string]$checklistEntry.status
        $mobileCollectionChecklistStatuses[$checklistName] = if ($checklistStatus -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
        if ($checklistStatus -ne "uncollected") {
            $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_collection_checklist.$checklistName.status is not uncollected")
        }
        if ([string]::IsNullOrWhiteSpace([string]$checklistEntry.overclaim_guard)) {
            $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_collection_checklist.$checklistName.overclaim_guard is missing")
        }
        if ([string]::IsNullOrWhiteSpace([string]$checklistEntry.reviewer_check)) {
            $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_collection_checklist.$checklistName.reviewer_check is missing")
        }
    }
    if ($mobileArtifactMismatches.Count -gt 0) {
        $contractFailures.Add("latest mobile LAN/WSS preflight artifact failed redacted contract validation")
    }
    [ordered]@{
        found = $true
        path = $latestMobile.path
        last_write_utc = $latestMobile.last_write_utc
        source_contract_status = if ($mobileArtifactMismatches.Count -eq 0) { "valid_redacted_summary" } else { "source_contract_mismatch" }
        mismatch_reasons = @($mobileArtifactMismatches)
        result = if ($mobileArtifactMismatches.Count -eq 0 -and $mobileResult -in $allowedMobileResults) { $mobileResult } elseif ($mobileResult -in $allowedMobileResults) { "source_contract_mismatch" } else { "invalid_redacted" }
        generated_at_utc = if ($mobileArtifactMismatches -notcontains "generated_at_utc is not a timestamp") { $mobileGeneratedAt } else { "invalid_redacted" }
        real_device_evidence_status = if ($mobileRealDeviceEvidenceStatus -eq "uncollected_fail_closed") { $mobileRealDeviceEvidenceStatus } else { "invalid_redacted" }
        real_device_evidence_collected = $false
        no_phone_preflight_claim = if ($mobileNoPhoneClaim -eq "not_real_device_pass") { $mobileNoPhoneClaim } else { "invalid_redacted" }
        backend = [ordered]@{
            host_redacted = Get-SafeMobileHostLabel $mobileHostRedacted
            public_base_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobilePublicBaseUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileHttpOrigin $mobilePublicBaseUrlRedacted }
            websocket_approvals_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileApprovalsUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileApprovalsUrlRedacted }
            websocket_remote_screen_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileRemoteScreenUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileRemoteScreenUrlRedacted }
            websocket_remote_input_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileRemoteInputUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileRemoteInputUrlRedacted }
        }
        lan_tls = [ordered]@{
            enabled = Get-StrictJsonBoolValue $latestMobile.data.lan_tls.enabled
            tls_material_valid = Get-StrictJsonBoolValue $latestMobile.data.lan_tls.tls_material_valid
            tls_host_valid = Get-StrictJsonBoolValue $latestMobile.data.lan_tls.tls_host_valid
        }
        qr_payload_shape = [ordered]@{
            transport_security_status = if ($mobileArtifactMismatches.Count -eq 0 -and $mobileTransportSecurityStatus -in @("https_ready_preflight", "https_wss_preflight_blocked")) { $mobileTransportSecurityStatus } elseif ($mobileTransportSecurityStatus -in @("https_ready_preflight", "https_wss_preflight_blocked")) { "source_contract_mismatch" } else { "invalid_redacted" }
            transport_security_tls_ready = Get-StrictJsonBoolValue $latestMobile.data.qr_payload_shape.transport_security_tls_ready
            websocket_approvals_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrApprovalsUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileQrApprovalsUrlRedacted }
            websocket_remote_screen_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrRemoteScreenUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileQrRemoteScreenUrlRedacted }
            websocket_remote_input_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrRemoteInputUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileQrRemoteInputUrlRedacted }
        }
        manual_real_device_evidence_template = [ordered]@{
            template_status = if ([string]$latestMobile.data.manual_real_device_evidence_template.template_status -eq "manual_real_device_evidence_required") { "manual_real_device_evidence_required" } else { "invalid_redacted" }
            real_device_result = if ([string]$latestMobile.data.manual_real_device_evidence_template.real_device_result -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
            real_device_evidence_status = if ([string]$latestMobile.data.manual_real_device_evidence_template.real_device_evidence_status -eq "uncollected_fail_closed") { "uncollected_fail_closed" } else { "invalid_redacted" }
            real_device_evidence_collected = $false
            no_phone_preflight_claim = if ([string]$latestMobile.data.manual_real_device_evidence_template.no_phone_preflight_claim -eq "not_real_device_pass") { "not_real_device_pass" } else { "invalid_redacted" }
            may_be_recorded_as = if ([string]$latestMobile.data.manual_real_device_evidence_template.may_be_recorded_as -eq "preflight/config evidence only") { "preflight/config evidence only" } else { "invalid_redacted" }
            must_not_be_recorded_as = if ([string]$latestMobile.data.manual_real_device_evidence_template.must_not_be_recorded_as -eq "real-device pass evidence") { "real-device pass evidence" } else { "invalid_redacted" }
            claim_controls = [ordered]@{
                real_device_pass_claim_allowed = $false
                preflight_ready_is_pass = $false
            }
            artifact_collection_rules = [ordered]@{
                review_required_before_pass_claim = Get-StrictJsonBoolValue $latestMobile.data.manual_real_device_evidence_template.artifact_collection_rules.review_required_before_pass_claim
            }
            fields = [ordered]@{
                camera_qr_path_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.camera_qr_path_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                actual_device_https_wss_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.actual_device_https_wss_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                approval_wss_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.approval_wss_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                approval_artifact_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.approval_artifact_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_screen_wss_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_screen_wss_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_screen_artifact_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_screen_artifact_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_input_wss_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_input_wss_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_input_artifact_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_input_artifact_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                certificate_trust_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.certificate_trust_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_input_grant_revoke_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_input_grant_revoke_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_input_grant_expiry_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_input_grant_expiry_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                grant_revoke_expiry_artifact_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.grant_revoke_expiry_artifact_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                artifact_redaction_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.artifact_redaction_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
            }
            collection_checklist_statuses = $mobileCollectionChecklistStatuses
        }
        issues_count = Get-ArrayCount $latestMobile.data.issues
        warnings_count = Get-ArrayCount $latestMobile.data.warnings
    }
}
elseif ($latestMobile.found) {
    $contractFailures.Add("latest mobile LAN/WSS preflight artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestMobile.path
        last_write_utc = $latestMobile.last_write_utc
        source_contract_status = "source_contract_mismatch"
        parse_error = $latestMobile.error
        result = "source_contract_mismatch"
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $mobileEvidenceRootPath
        result = "not_collected_by_this_packet"
    }
}

$androidRealDeviceTemplateCheckNames = @(
    "apk_installed",
    "camera_qr_pairing",
    "https_api_reachability",
    "certificate_trust_path",
    "approval_wss",
    "remote_screen_wss",
    "remote_input_wss",
    "click_input_approval",
    "text_input_approval",
    "key_pagedown_approval",
    "mobile_end_control_readonly",
    "desktop_revoke_readonly",
    "grant_expiry_readonly",
    "background_or_lockscreen_privacy",
    "artifact_redaction_review"
)
$latestAndroidRealDeviceTemplate = Find-LatestJsonArtifact $androidRealDeviceEvidenceRootPath "android-real-device-evidence.redacted.template.json"
$androidRealDeviceTemplateLatestSummary = if ($latestAndroidRealDeviceTemplate.found -and $null -ne $latestAndroidRealDeviceTemplate.data) {
    $androidTemplateMismatches = New-Object System.Collections.Generic.List[string]
    $androidTemplateCheckStatuses = [ordered]@{}

    if ([string]$latestAndroidRealDeviceTemplate.data.artifact_type -ne "android-real-device-remote-control-evidence") {
        $androidTemplateMismatches.Add("artifact_type is not android-real-device-remote-control-evidence")
    }
    if ([string]$latestAndroidRealDeviceTemplate.data.template_status -ne "manual_real_device_evidence_required") {
        $androidTemplateMismatches.Add("template_status is not manual_real_device_evidence_required")
    }
    if ([string]$latestAndroidRealDeviceTemplate.data.real_device_result -ne "uncollected") {
        $androidTemplateMismatches.Add("real_device_result is not uncollected")
    }
    if (-not (Test-JsonFalse $latestAndroidRealDeviceTemplate.data.claim_controls.real_device_pass_claim_allowed)) {
        $androidTemplateMismatches.Add("claim_controls.real_device_pass_claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestAndroidRealDeviceTemplate.data.claim_controls.binding_ref_used_for_shareable_artifacts)) {
        $androidTemplateMismatches.Add("claim_controls.binding_ref_used_for_shareable_artifacts is not false")
    }
    if (-not (Test-JsonFalse $latestAndroidRealDeviceTemplate.data.claim_controls.raw_device_grant_ids_local_only)) {
        $androidTemplateMismatches.Add("claim_controls.raw_device_grant_ids_local_only is not false")
    }
    if ([string]$latestAndroidRealDeviceTemplate.data.shareable_identity_policy.public_remote_input_correlation -notmatch "binding_ref") {
        $androidTemplateMismatches.Add("shareable_identity_policy.public_remote_input_correlation does not require binding_ref")
    }
    foreach ($checkName in $androidRealDeviceTemplateCheckNames) {
        $check = $latestAndroidRealDeviceTemplate.data.checks.$checkName
        $checkStatus = [string]$check.status
        $androidTemplateCheckStatuses[$checkName] = if ($checkStatus -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
        if ($checkStatus -ne "uncollected") {
            $androidTemplateMismatches.Add("checks.$checkName.status is not uncollected")
        }
        if ([string]::IsNullOrWhiteSpace([string]$check.required_evidence)) {
            $androidTemplateMismatches.Add("checks.$checkName.required_evidence is missing")
        }
        if ([string]::IsNullOrWhiteSpace([string]$check.overclaim_guard)) {
            $androidTemplateMismatches.Add("checks.$checkName.overclaim_guard is missing")
        }
    }
    foreach ($redactionFlag in @("tokens_absent", "pairing_codes_absent", "raw_hosts_absent", "raw_device_ids_absent", "raw_grant_ids_absent", "private_paths_absent", "binding_ref_or_redacted_active_grant_label_used")) {
        if (-not (Test-JsonFalse $latestAndroidRealDeviceTemplate.data.redaction.$redactionFlag)) {
            $androidTemplateMismatches.Add("redaction.$redactionFlag is not false in the fail-closed template")
        }
    }
    if (-not (Test-ArrayContainsText $latestAndroidRealDeviceTemplate.data.must_not_claim "real-device Android remote-control pass")) {
        $androidTemplateMismatches.Add("must_not_claim is missing real-device Android remote-control pass")
    }
    if ($androidTemplateMismatches.Count -gt 0) {
        $contractFailures.Add("latest Android real-device evidence template artifact failed fail-closed contract validation")
    }

    [ordered]@{
        found = $true
        path = $latestAndroidRealDeviceTemplate.path
        last_write_utc = $latestAndroidRealDeviceTemplate.last_write_utc
        source_contract_status = if ($androidTemplateMismatches.Count -eq 0) { "valid_fail_closed_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($androidTemplateMismatches)
        template_status = if ([string]$latestAndroidRealDeviceTemplate.data.template_status -eq "manual_real_device_evidence_required") { "manual_real_device_evidence_required" } else { "invalid_redacted" }
        real_device_result = if ([string]$latestAndroidRealDeviceTemplate.data.real_device_result -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
        review_status = if ([string]$latestAndroidRealDeviceTemplate.data.review.status -eq "unreviewed") { "unreviewed" } else { "invalid_redacted" }
        pass_claim_allowed = $false
        redaction_reviewed = $false
        evidence_artifacts_reviewed = $false
        check_statuses = $androidTemplateCheckStatuses
        build_environment = [ordered]@{
            local_apk_build_ready = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.local_apk_build_ready
            local_eas_cli_declared = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.local_eas_cli_declared
            local_eas_cli_binary_present = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.local_eas_cli_binary_present
            expo_token_present = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.expo_token_present
            eas_cloud_auth_verified = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.eas_cloud_auth_verified
            build_blocker_summary = Redact-TextValue ([string]$latestAndroidRealDeviceTemplate.data.build_environment.build_blocker_summary)
        }
        shareable_identity_policy = [ordered]@{
            public_remote_input_correlation = Redact-TextValue ([string]$latestAndroidRealDeviceTemplate.data.shareable_identity_policy.public_remote_input_correlation)
            raw_device_id_storage = Redact-TextValue ([string]$latestAndroidRealDeviceTemplate.data.shareable_identity_policy.raw_device_id_storage)
            raw_grant_id_storage = Redact-TextValue ([string]$latestAndroidRealDeviceTemplate.data.shareable_identity_policy.raw_grant_id_storage)
        }
        must_not_claim = @($latestAndroidRealDeviceTemplate.data.must_not_claim | ForEach-Object { Redact-TextValue ([string]$_) })
    }
}
elseif ($latestAndroidRealDeviceTemplate.found) {
    $contractFailures.Add("latest Android real-device evidence template artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestAndroidRealDeviceTemplate.path
        last_write_utc = $latestAndroidRealDeviceTemplate.last_write_utc
        source_contract_status = "source_contract_mismatch"
        parse_error = $latestAndroidRealDeviceTemplate.error
        template_status = "source_contract_mismatch"
        pass_claim_allowed = $false
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $androidRealDeviceEvidenceRootPath
        source_contract_status = "not_collected_by_this_packet"
        template_status = "not_collected_by_this_packet"
        real_device_result = "uncollected"
        pass_claim_allowed = $false
    }
}

$latestAndroidReleaseGate = Find-LatestJsonArtifact $androidReleaseGateEvidenceRootPath "android-release-gate.redacted.json"
$androidReleaseGateLatestSummary = if ($latestAndroidReleaseGate.found -and $null -ne $latestAndroidReleaseGate.data) {
    $androidGateMismatches = New-Object System.Collections.Generic.List[string]
    $androidArtifactType = [string]$latestAndroidReleaseGate.data.artifact_type
    $androidGeneratedBy = [string]$latestAndroidReleaseGate.data.generated_by
    $androidGeneratedAt = [string]$latestAndroidReleaseGate.data.generated_at_utc
    $androidStatus = [string]$latestAndroidReleaseGate.data.status
    $allowedAndroidStatuses = @("preflight_ready_not_release", "blocked", "passed")
    $androidReleaseReady = Test-JsonTrue $latestAndroidReleaseGate.data.release_ready
    $androidPreflightOnly = Test-JsonTrue $latestAndroidReleaseGate.data.preflight_only
    $installClaimAllowed = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.installable_android_app_claim_allowed
    $remoteClaimAllowed = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.real_device_remote_control_claim_allowed
    $expoPreviewIsNotRelease = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.expo_preview_is_not_release
    $requiresApkEvidence = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.requires_reviewed_apk_install_evidence
    $requiresWssEvidence = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.requires_reviewed_https_wss_remote_control_evidence
    $artifactLabel = Redact-DisplayLabel ([string]$latestAndroidReleaseGate.data.android_artifact.label)
    $artifactProvided = Test-JsonTrue $latestAndroidReleaseGate.data.android_artifact.provided
    $artifactSha256 = [string]$latestAndroidReleaseGate.data.android_artifact.sha256
    $installableApk = Test-JsonTrue $latestAndroidReleaseGate.data.android_artifact.installable_apk
    $apkZipHeaderValid = Test-JsonTrue $latestAndroidReleaseGate.data.android_artifact.apk_zip_header_valid
    $artifactBytes = Get-StrictJsonNonNegativeIntegerOrZero $latestAndroidReleaseGate.data.android_artifact.bytes
    $artifactGateEvaluated = Test-JsonTrue $latestAndroidReleaseGate.data.artifact_gate.evaluated
    $artifactGatePassed = Test-JsonTrue $latestAndroidReleaseGate.data.artifact_gate.passed
    $realDeviceGateEvaluated = Test-JsonTrue $latestAndroidReleaseGate.data.real_device_gate.evaluated
    $realDeviceGatePassed = Test-JsonTrue $latestAndroidReleaseGate.data.real_device_gate.passed
    $sourceConfigPassed = Test-JsonTrue $latestAndroidReleaseGate.data.source_config.passed

    if ($androidArtifactType -ne "android-release-gate-summary") {
        $androidGateMismatches.Add("artifact_type is not android-release-gate-summary")
    }
    if ($androidGeneratedBy -ne "scripts/verify_android_release_gate.ps1") {
        $androidGateMismatches.Add("generated_by is not scripts/verify_android_release_gate.ps1")
    }
    if (-not (Test-UtcTimestampValue $androidGeneratedAt)) {
        $androidGateMismatches.Add("generated_at_utc is not a UTC timestamp")
    }
    if ($androidStatus -notin $allowedAndroidStatuses) {
        $androidGateMismatches.Add("status is not an allowed Android release gate status")
    }
    if ($androidStatus -eq "preflight_ready_not_release" -and -not $androidPreflightOnly) {
        $androidGateMismatches.Add("preflight_ready_not_release Android gate must set preflight_only=true")
    }
    if ($androidStatus -eq "passed" -and $androidPreflightOnly) {
        $androidGateMismatches.Add("passed Android gate must set preflight_only=false")
    }
    if ($androidPreflightOnly) {
        if ($artifactGateEvaluated) {
            $androidGateMismatches.Add("preflight Android gate must not evaluate artifact_gate")
        }
        if ($artifactGatePassed) {
            $androidGateMismatches.Add("preflight Android gate must not set artifact_gate.passed=true")
        }
        if ($realDeviceGateEvaluated) {
            $androidGateMismatches.Add("preflight Android gate must not evaluate real_device_gate")
        }
        if ($realDeviceGatePassed) {
            $androidGateMismatches.Add("preflight Android gate must not set real_device_gate.passed=true")
        }
    }
    if (-not $sourceConfigPassed) {
        $androidGateMismatches.Add("source_config.passed is not true")
    }
    if (-not $expoPreviewIsNotRelease) {
        $androidGateMismatches.Add("claim_controls.expo_preview_is_not_release is not true")
    }
    if (-not $requiresApkEvidence) {
        $androidGateMismatches.Add("claim_controls.requires_reviewed_apk_install_evidence is not true")
    }
    if (-not $requiresWssEvidence) {
        $androidGateMismatches.Add("claim_controls.requires_reviewed_https_wss_remote_control_evidence is not true")
    }
    if ($androidStatus -eq "passed") {
        if (-not $androidReleaseReady) {
            $androidGateMismatches.Add("passed Android gate must set release_ready=true")
        }
        if (-not $installClaimAllowed) {
            $androidGateMismatches.Add("passed Android gate must allow installable Android app claims")
        }
        if (-not $remoteClaimAllowed) {
            $androidGateMismatches.Add("passed Android gate must allow real-device remote-control claims")
        }
        if ($artifactSha256 -notmatch "^[a-fA-F0-9]{64}$") {
            $androidGateMismatches.Add("passed Android gate must include a 64-character android_artifact.sha256")
        }
        if ($artifactBytes -lt 1048576) {
            $androidGateMismatches.Add("passed Android gate must include an Android artifact of at least 1 MiB")
        }
        if (-not (Test-EmptyArrayValue $latestAndroidReleaseGate.data.source_config.issues)) {
            $androidGateMismatches.Add("passed Android gate must have no source_config issues")
        }
        if (-not (Test-EmptyArrayValue $latestAndroidReleaseGate.data.artifact_gate.issues)) {
            $androidGateMismatches.Add("passed Android gate must have no artifact_gate issues")
        }
        if (-not (Test-EmptyArrayValue $latestAndroidReleaseGate.data.real_device_gate.issues)) {
            $androidGateMismatches.Add("passed Android gate must have no real_device_gate issues")
        }
        if (-not ($artifactProvided -and $installableApk -and $apkZipHeaderValid -and $artifactBytes -ge 1048576 -and $artifactGateEvaluated -and $artifactGatePassed -and $realDeviceGateEvaluated -and $realDeviceGatePassed)) {
            $androidGateMismatches.Add("passed Android gate must include installable APK and real-device evidence gates")
        }
    }
    else {
        if ($androidReleaseReady) {
            $androidGateMismatches.Add("non-passed Android gate must not set release_ready=true")
        }
        if ($installClaimAllowed) {
            $androidGateMismatches.Add("non-passed Android gate must not allow installable Android app claims")
        }
        if ($remoteClaimAllowed) {
            $androidGateMismatches.Add("non-passed Android gate must not allow real-device remote-control claims")
        }
        if (-not (Test-ArrayContainsText $latestAndroidReleaseGate.data.must_not_claim "installable Android app release pass")) {
            $androidGateMismatches.Add("non-passed Android gate must include installable Android app release pass in must_not_claim")
        }
        if (-not (Test-ArrayContainsText $latestAndroidReleaseGate.data.must_not_claim "real-device Android remote-control pass")) {
            $androidGateMismatches.Add("non-passed Android gate must include real-device Android remote-control pass in must_not_claim")
        }
    }

    if ($androidGateMismatches.Count -gt 0) {
        $contractFailures.Add("latest Android release gate artifact failed redacted contract validation")
    }

    $androidGateSourceContractValid = $androidGateMismatches.Count -eq 0

    [ordered]@{
        found = $true
        path = $latestAndroidReleaseGate.path
        last_write_utc = $latestAndroidReleaseGate.last_write_utc
        source_contract_status = if ($androidGateSourceContractValid) { "valid_redacted_summary" } else { "source_contract_mismatch" }
        mismatch_reasons = @($androidGateMismatches)
        status = if ($androidStatus -in $allowedAndroidStatuses -and $androidGateSourceContractValid) { $androidStatus } elseif ($androidStatus -in $allowedAndroidStatuses) { "source_contract_mismatch" } else { "invalid_redacted" }
        release_ready = if ($androidGateSourceContractValid) { $androidReleaseReady } else { $false }
        preflight_only = if ($androidGateSourceContractValid) { $androidPreflightOnly } else { $false }
        source_config_passed = if ($androidGateSourceContractValid) { $sourceConfigPassed } else { $false }
        android_artifact = [ordered]@{
            provided = if ($androidGateSourceContractValid) { $artifactProvided } else { $false }
            label = $artifactLabel
            bytes = if ($androidGateSourceContractValid) { $artifactBytes } else { 0 }
            installable_apk = if ($androidGateSourceContractValid) { $installableApk } else { $false }
            apk_zip_header_valid = if ($androidGateSourceContractValid) { $apkZipHeaderValid } else { $false }
        }
        artifact_gate_evaluated = if ($androidGateSourceContractValid) { $artifactGateEvaluated } else { $false }
        artifact_gate_passed = if ($androidGateSourceContractValid) { $artifactGatePassed } else { $false }
        real_device_gate_evaluated = if ($androidGateSourceContractValid) { $realDeviceGateEvaluated } else { $false }
        real_device_gate_passed = if ($androidGateSourceContractValid) { $realDeviceGatePassed } else { $false }
        real_device_evidence_label = Redact-DisplayLabel ([string]$latestAndroidReleaseGate.data.real_device_gate.evidence_label)
        claim_controls = [ordered]@{
            installable_android_app_claim_allowed = if ($androidGateSourceContractValid) { $installClaimAllowed } else { $false }
            real_device_remote_control_claim_allowed = if ($androidGateSourceContractValid) { $remoteClaimAllowed } else { $false }
            expo_preview_is_not_release = if ($androidGateSourceContractValid) { $expoPreviewIsNotRelease } else { $false }
            requires_reviewed_apk_install_evidence = if ($androidGateSourceContractValid) { $requiresApkEvidence } else { $false }
            requires_reviewed_https_wss_remote_control_evidence = if ($androidGateSourceContractValid) { $requiresWssEvidence } else { $false }
        }
        warnings_count = Get-ArrayCount $latestAndroidReleaseGate.data.warnings
        must_not_claim = @($latestAndroidReleaseGate.data.must_not_claim | ForEach-Object { Redact-TextValue ([string]$_) })
    }
}
elseif ($latestAndroidReleaseGate.found) {
    $contractFailures.Add("latest Android release gate artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestAndroidReleaseGate.path
        last_write_utc = $latestAndroidReleaseGate.last_write_utc
        source_contract_status = "source_contract_mismatch"
        parse_error = $latestAndroidReleaseGate.error
        status = "source_contract_mismatch"
        release_ready = $false
        claim_controls = [ordered]@{
            installable_android_app_claim_allowed = $false
            real_device_remote_control_claim_allowed = $false
        }
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $androidReleaseGateEvidenceRootPath
        source_contract_status = "not_collected_by_this_packet"
        status = "not_collected_by_this_packet"
        release_ready = $false
        claim_controls = [ordered]@{
            installable_android_app_claim_allowed = $false
            real_device_remote_control_claim_allowed = $false
        }
    }
}

$ollamaContractFiles = @(
    "backend/tests/test_ollama_service.py",
    "backend/tests/test_ollama_install_endpoint.py"
)
$ollamaCounts = @($ollamaContractFiles | ForEach-Object { Count-TestContracts $_ })
$ollamaContractCount = 0
foreach ($item in $ollamaCounts) {
    $ollamaContractCount += [int]$item.test_contract_count
    if (-not $item.exists) {
        $contractFailures.Add("Ollama/local-model contract file is missing: $($item.path)")
    }
}

$localModelTemplateNeedles = @(
    "local-model-clean-machine-evidence.redacted.json",
    "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS",
    'clean_machine_signoff = $false',
    'local_model_install_pass = $false',
    'local_model_start_pass = $false',
    'local_model_pull_pass = $false',
    'local_model_task_smoke_pass = $false',
    'real_install_start_pull_pass = $false',
    'template_is_clean_machine_pass = $false',
    'dev_smoke_is_clean_machine_pass = $false',
    "manual_clean_machine_local_model_evidence_required",
    "artifact_build_profile",
    "clean_machine_run",
    "task_smoke",
    "true local model install pass",
    "true local model pull pass",
    "template/dev smoke clean-machine pass"
)
$localModelTemplateContract = Get-SourceContract "scripts/collect_local_model_clean_machine_evidence_template.ps1" $localModelTemplateNeedles
if (-not $localModelTemplateContract.required_markers_present) {
    $contractFailures.Add("local-model clean-machine evidence template source contract is missing required non-signoff markers")
}
$latestLocalModelTemplate = Find-LatestJsonArtifact $localModelCleanMachineEvidenceRootPath "local-model-clean-machine-evidence.redacted.json"
$localModelTemplateLatestSummary = if ($latestLocalModelTemplate.found -and $null -ne $latestLocalModelTemplate.data) {
    $localModelTemplateMismatches = New-Object System.Collections.Generic.List[string]
    $localModelTemplateMarker = [string]$latestLocalModelTemplate.data.marker
    $localModelTemplateStatus = [string]$latestLocalModelTemplate.data.summary.template_status
    $evidenceTemplateStatus = [string]$latestLocalModelTemplate.data.evidence_template.template_status
    $localModelMissingFieldCount = Get-ArrayCount $latestLocalModelTemplate.data.summary.missing_required_fields
    if ($localModelTemplateMarker -ne "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS") {
        $localModelTemplateMismatches.Add("marker is missing or not NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS")
    }
    if (-not (Test-JsonIntegerOne $latestLocalModelTemplate.data.schema_version)) {
        $localModelTemplateMismatches.Add("schema_version is not 1")
    }
    if ([string]$latestLocalModelTemplate.data.generated_by -ne "scripts/collect_local_model_clean_machine_evidence_template.ps1") {
        $localModelTemplateMismatches.Add("generated_by is not the local-model clean-machine helper")
    }
    if ($localModelTemplateStatus -notin @("manual_review_ready", "blocked_reason_recorded", "blocked_missing_required_fields")) {
        $localModelTemplateMismatches.Add("summary.template_status is not an allowed non-signoff status")
    }
    if (-not (Test-JsonNonNegativeInteger $latestLocalModelTemplate.data.summary.missing_required_fields_count)) {
        $localModelTemplateMismatches.Add("summary.missing_required_fields_count is not a non-negative JSON integer")
    }
    elseif ([int64]$latestLocalModelTemplate.data.summary.missing_required_fields_count -ne [int64]$localModelMissingFieldCount) {
        $localModelTemplateMismatches.Add("summary.missing_required_fields_count does not match missing_required_fields")
    }
    if ($localModelTemplateStatus -eq "manual_review_ready" -and $localModelMissingFieldCount -ne 0) {
        $localModelTemplateMismatches.Add("manual_review_ready local-model template still has missing required fields")
    }
    if ($evidenceTemplateStatus -ne "manual_clean_machine_local_model_evidence_required") {
        $localModelTemplateMismatches.Add("evidence_template.template_status is not manual_clean_machine_local_model_evidence_required")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.clean_machine_signoff)) {
        $localModelTemplateMismatches.Add("summary.clean_machine_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.local_model_install_pass)) {
        $localModelTemplateMismatches.Add("summary.local_model_install_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.local_model_start_pass)) {
        $localModelTemplateMismatches.Add("summary.local_model_start_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.local_model_pull_pass)) {
        $localModelTemplateMismatches.Add("summary.local_model_pull_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.local_model_task_smoke_pass)) {
        $localModelTemplateMismatches.Add("summary.local_model_task_smoke_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.real_install_start_pull_pass)) {
        $localModelTemplateMismatches.Add("summary.real_install_start_pull_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.template_is_clean_machine_pass)) {
        $localModelTemplateMismatches.Add("summary.template_is_clean_machine_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.dev_smoke_is_clean_machine_pass)) {
        $localModelTemplateMismatches.Add("summary.dev_smoke_is_clean_machine_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.release_candidate_signoff)) {
        $localModelTemplateMismatches.Add("summary.release_candidate_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.starts_product_processes)) {
        $localModelTemplateMismatches.Add("readonly_scope.starts_product_processes is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.performs_network_requests)) {
        $localModelTemplateMismatches.Add("readonly_scope.performs_network_requests is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.installs_runtime)) {
        $localModelTemplateMismatches.Add("readonly_scope.installs_runtime is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.starts_runtime)) {
        $localModelTemplateMismatches.Add("readonly_scope.starts_runtime is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.pulls_models)) {
        $localModelTemplateMismatches.Add("readonly_scope.pulls_models is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.runs_model_inference)) {
        $localModelTemplateMismatches.Add("readonly_scope.runs_model_inference is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.redaction.raw_logs_included)) {
        $localModelTemplateMismatches.Add("redaction.raw_logs_included is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.redaction.secrets_or_tokens_read)) {
        $localModelTemplateMismatches.Add("redaction.secrets_or_tokens_read is not false")
    }
    if (-not (Test-JsonTrue $latestLocalModelTemplate.data.redaction.urls_redacted)) {
        $localModelTemplateMismatches.Add("redaction.urls_redacted is not true")
    }
    if ([string]$latestLocalModelTemplate.data.evidence_template.runtime.status -ne "unverified_by_this_helper") {
        $localModelTemplateMismatches.Add("evidence_template.runtime.status is not unverified_by_this_helper")
    }
    if ($localModelTemplateStatus -eq "manual_review_ready") {
        if (-not (Test-MeaningfulEvidenceValue ([string]$latestLocalModelTemplate.data.evidence_template.runtime.name))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing runtime.name")
        }
        if (-not (Test-MeaningfulEvidenceValue ([string]$latestLocalModelTemplate.data.evidence_template.runtime.version))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing runtime.version")
        }
    }
    if ([string]$latestLocalModelTemplate.data.evidence_template.model.status -ne "unverified_by_this_helper") {
        $localModelTemplateMismatches.Add("evidence_template.model.status is not unverified_by_this_helper")
    }
    if ($localModelTemplateStatus -eq "manual_review_ready") {
        if (-not (Test-MeaningfulEvidenceValue ([string]$latestLocalModelTemplate.data.evidence_template.model.name))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing model.name")
        }
        if (-not (Test-MeaningfulEvidenceValue ([string]$latestLocalModelTemplate.data.evidence_template.model.version))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing model.version")
        }
    }
    $artifactBuildProfile = $latestLocalModelTemplate.data.evidence_template.artifact_build_profile
    if ($null -eq $artifactBuildProfile) {
        $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile is missing")
    }
    else {
        if (-not (Test-LocalModelArtifactBuildProfileStatus ([string]$artifactBuildProfile.status))) {
            $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile.status is not an allowed fail-closed status")
        }
        if ($localModelTemplateStatus -eq "manual_review_ready" -and [string]$artifactBuildProfile.status -ne "recorded_unverified_by_this_helper") {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template artifact/build/profile status is not recorded")
        }
        if ([string]$artifactBuildProfile.artifact.status -ne "unverified_by_this_helper") {
            $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile.artifact.status is not unverified_by_this_helper")
        }
        if ($localModelTemplateStatus -eq "manual_review_ready" -and -not (Test-MeaningfulEvidenceValue ([string]$artifactBuildProfile.artifact.label))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing artifact label")
        }
        if ([string]$artifactBuildProfile.build.status -ne "unverified_by_this_helper") {
            $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile.build.status is not unverified_by_this_helper")
        }
        if ($localModelTemplateStatus -eq "manual_review_ready" -and -not (Test-MeaningfulEvidenceValue ([string]$artifactBuildProfile.build.identifier))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing build identifier")
        }
        if ([string]$artifactBuildProfile.profile.status -ne "unverified_by_this_helper") {
            $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile.profile.status is not unverified_by_this_helper")
        }
        if ($localModelTemplateStatus -eq "manual_review_ready" -and -not (Test-MeaningfulEvidenceValue ([string]$artifactBuildProfile.profile.label))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing profile label")
        }
    }
    $cleanMachineRun = $latestLocalModelTemplate.data.evidence_template.clean_machine_run
    if ($null -eq $cleanMachineRun) {
        $localModelTemplateMismatches.Add("evidence_template.clean_machine_run is missing")
    }
    else {
        foreach ($stepName in @("install", "start", "pull", "task_smoke")) {
            $step = $cleanMachineRun.$stepName
            if ($null -eq $step) {
                $localModelTemplateMismatches.Add("evidence_template.clean_machine_run.$stepName is missing")
            }
            else {
                if (-not (Test-LocalModelStepStatus ([string]$step.status))) {
                    $localModelTemplateMismatches.Add("evidence_template.clean_machine_run.$stepName.status is not an allowed fail-closed status")
                }
                if (-not (Test-JsonFalse $step.pass_verified_by_this_helper)) {
                    $localModelTemplateMismatches.Add("evidence_template.clean_machine_run.$stepName.pass_verified_by_this_helper is not false")
                }
                if (-not (Test-JsonFalse $step.clean_machine_pass)) {
                    $localModelTemplateMismatches.Add("evidence_template.clean_machine_run.$stepName.clean_machine_pass is not false")
                }
                if ($localModelTemplateStatus -eq "manual_review_ready") {
                    if ([string]$step.status -ne "manual_outcome_recorded_unverified_by_this_helper") {
                        $localModelTemplateMismatches.Add("manual_review_ready local-model template $stepName status is not a recorded manual outcome")
                    }
                    if (-not (Test-MeaningfulEvidenceValue ([string]$step.outcome))) {
                        $localModelTemplateMismatches.Add("manual_review_ready local-model template $stepName outcome is missing")
                    }
                }
            }
        }
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "true local model install pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing true local model install pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "true local model start pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing true local model start pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "true local model pull pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing true local model pull pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "true local model task-smoke pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing true local model task-smoke pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "clean-machine local-model readiness")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing clean-machine local-model readiness")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "template/dev smoke clean-machine pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing template/dev smoke clean-machine pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "release-candidate sign-off")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing release-candidate sign-off")
    }
    if ($localModelTemplateMismatches.Count -gt 0) {
        $contractFailures.Add("latest local-model clean-machine helper artifact failed fail-closed validation")
    }
    $safeLocalModelMarker = if ($localModelTemplateMarker -eq "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS") { $localModelTemplateMarker } else { "invalid_redacted" }
    $safeLocalModelStatus = if ($localModelTemplateStatus -in @("manual_review_ready", "blocked_reason_recorded", "blocked_missing_required_fields")) { $localModelTemplateStatus } else { "invalid_redacted" }
    $safeArtifactBuildProfile = [ordered]@{
        status = Get-SafeLocalModelArtifactBuildProfileStatus ([string]$latestLocalModelTemplate.data.evidence_template.artifact_build_profile.status)
        artifact_under_test = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.artifact_build_profile.artifact.label)
        build_identifier = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.artifact_build_profile.build.identifier)
        profile_under_test = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.artifact_build_profile.profile.label)
    }
    $safeLocalModelRuntime = [ordered]@{
        name = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.runtime.name)
        version = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.runtime.version)
        status = if ([string]$latestLocalModelTemplate.data.evidence_template.runtime.status -eq "unverified_by_this_helper") { "unverified_by_this_helper" } else { "invalid_redacted" }
    }
    $safeLocalModelModel = [ordered]@{
        name = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.model.name)
        version = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.model.version)
        status = if ([string]$latestLocalModelTemplate.data.evidence_template.model.status -eq "unverified_by_this_helper") { "unverified_by_this_helper" } else { "invalid_redacted" }
    }
    $safeCleanMachineRun = [ordered]@{
        install = New-SafeLocalModelStepSummary $latestLocalModelTemplate.data.evidence_template.clean_machine_run.install
        start = New-SafeLocalModelStepSummary $latestLocalModelTemplate.data.evidence_template.clean_machine_run.start
        pull = New-SafeLocalModelStepSummary $latestLocalModelTemplate.data.evidence_template.clean_machine_run.pull
        task_smoke = New-SafeLocalModelStepSummary $latestLocalModelTemplate.data.evidence_template.clean_machine_run.task_smoke
    }
    [ordered]@{
        found = $true
        path = $latestLocalModelTemplate.path
        last_write_utc = $latestLocalModelTemplate.last_write_utc
        marker = $safeLocalModelMarker
        source_contract_status = if ($localModelTemplateMismatches.Count -eq 0) { "valid_not_signoff_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($localModelTemplateMismatches)
        template_status = $safeLocalModelStatus
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        real_install_start_pull_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        release_candidate_signoff = $false
        artifact_build_profile = $safeArtifactBuildProfile
        runtime = $safeLocalModelRuntime
        model = $safeLocalModelModel
        clean_machine_run = $safeCleanMachineRun
        missing_required_fields_count = Get-ArrayCount $latestLocalModelTemplate.data.summary.missing_required_fields
        blocked_reason_count = Get-ArrayCount $latestLocalModelTemplate.data.evidence_template.blocked_reason_redacted
        observed_artifact_count = Get-ArrayCount $latestLocalModelTemplate.data.evidence_template.observed_artifacts_redacted
    }
}
elseif ($latestLocalModelTemplate.found) {
    $contractFailures.Add("latest local-model clean-machine helper artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestLocalModelTemplate.path
        last_write_utc = $latestLocalModelTemplate.last_write_utc
        parse_error = $latestLocalModelTemplate.error
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        real_install_start_pull_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        release_candidate_signoff = $false
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $localModelCleanMachineEvidenceRootPath
        template_status = "not_collected_by_this_packet"
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        real_install_start_pull_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        release_candidate_signoff = $false
    }
}

$diagnosticsNeedles = @(
    "support_package_redaction",
    'external_review["status"] == "manual_review_required"',
    'external_review["required_before_external_sharing"] is True',
    'external_review["public_safe"] is False',
    'assert ''"public_safe": false'' in package_text'
)
$diagnosticsContract = Get-SourceContract "backend/tests/test_system_diagnostics.py" $diagnosticsNeedles
if (-not $diagnosticsContract.required_markers_present) {
    $contractFailures.Add("diagnostics support-package external-review contract is missing required markers")
}
$latestDiagnosticsReview = Find-LatestJsonArtifact $diagnosticsReviewEvidenceRootPath "diagnostics-external-review.redacted.json"
$diagnosticsReviewLatestSummary = if ($latestDiagnosticsReview.found -and $null -ne $latestDiagnosticsReview.data) {
    $diagnosticsReviewMismatches = New-Object System.Collections.Generic.List[string]
    $diagnosticsReviewMarker = [string]$latestDiagnosticsReview.data.marker
    $reviewStatus = [string]$latestDiagnosticsReview.data.summary.review_status
    if ([string]::IsNullOrWhiteSpace($reviewStatus)) {
        $reviewStatus = [string]$latestDiagnosticsReview.data.summary.status
    }
    $allowedDiagnosticsReviewStatuses = @(
        "manual_external_review_template_ready",
        "blocked_missing_diagnostics_package",
        "blocked_unreadable_diagnostics_package",
        "blocked_contract_mismatch"
    )
    if ($diagnosticsReviewMarker -ne "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF") {
        $diagnosticsReviewMismatches.Add("marker is missing or not NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF")
    }
    if ($reviewStatus -notin $allowedDiagnosticsReviewStatuses) {
        $diagnosticsReviewMismatches.Add("review status is not a recognized fail-closed diagnostics review status")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.public_safe)) {
        $diagnosticsReviewMismatches.Add("summary.public_safe is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.summary.required_before_external_sharing)) {
        $diagnosticsReviewMismatches.Add("summary.required_before_external_sharing is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.human_review_signoff)) {
        $diagnosticsReviewMismatches.Add("summary.human_review_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.external_public_safe_signoff)) {
        $diagnosticsReviewMismatches.Add("summary.external_public_safe_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.template_is_human_signoff)) {
        $diagnosticsReviewMismatches.Add("summary.template_is_human_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.external_sharing_allowed)) {
        $diagnosticsReviewMismatches.Add("summary.external_sharing_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.claim_allowed)) {
        $diagnosticsReviewMismatches.Add("summary.claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.actual_package_content_review_completed)) {
        $diagnosticsReviewMismatches.Add("summary.actual_package_content_review_completed is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.summary.automated_template_only)) {
        $diagnosticsReviewMismatches.Add("summary.automated_template_only is not true")
    }
    if ($latestDiagnosticsReview.data.summary.review_fields_complete -isnot [bool]) {
        $diagnosticsReviewMismatches.Add("summary.review_fields_complete is not a JSON boolean")
    }
    elseif ([bool]$latestDiagnosticsReview.data.summary.review_fields_complete) {
        $diagnosticsReviewMismatches.Add("summary.review_fields_complete is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.summary.external_sharing_blocked)) {
        $diagnosticsReviewMismatches.Add("summary.external_sharing_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.summary.separate_human_content_review_required)) {
        $diagnosticsReviewMismatches.Add("summary.separate_human_content_review_required is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_scope.automated_redaction_template)) {
        $diagnosticsReviewMismatches.Add("review_scope.automated_redaction_template is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_scope.actual_package_content_review_completed)) {
        $diagnosticsReviewMismatches.Add("review_scope.actual_package_content_review_completed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_scope.automated_template_is_actual_package_content_review)) {
        $diagnosticsReviewMismatches.Add("review_scope.automated_template_is_actual_package_content_review is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_scope.actual_content_review_required_before_external_sharing)) {
        $diagnosticsReviewMismatches.Add("review_scope.actual_content_review_required_before_external_sharing is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_scope.review_fields_complete)) {
        $diagnosticsReviewMismatches.Add("review_scope.review_fields_complete is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_scope.external_sharing_blocked)) {
        $diagnosticsReviewMismatches.Add("review_scope.external_sharing_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_scope.separate_human_content_review_required)) {
        $diagnosticsReviewMismatches.Add("review_scope.separate_human_content_review_required is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.public_safe)) {
        $diagnosticsReviewMismatches.Add("claim_controls.public_safe is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.external_sharing_allowed)) {
        $diagnosticsReviewMismatches.Add("claim_controls.external_sharing_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.claim_allowed)) {
        $diagnosticsReviewMismatches.Add("claim_controls.claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.helper_can_approve_public_safety)) {
        $diagnosticsReviewMismatches.Add("claim_controls.helper_can_approve_public_safety is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.helper_can_authorize_external_sharing)) {
        $diagnosticsReviewMismatches.Add("claim_controls.helper_can_authorize_external_sharing is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.claim_controls.actual_content_review_required)) {
        $diagnosticsReviewMismatches.Add("claim_controls.actual_content_review_required is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.actual_content_review_completed)) {
        $diagnosticsReviewMismatches.Add("claim_controls.actual_content_review_completed is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.claim_controls.external_sharing_blocked)) {
        $diagnosticsReviewMismatches.Add("claim_controls.external_sharing_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.claim_controls.separate_human_content_review_required)) {
        $diagnosticsReviewMismatches.Add("claim_controls.separate_human_content_review_required is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.public_safe_approval_created)) {
        $diagnosticsReviewMismatches.Add("claim_controls.public_safe_approval_created is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.public_safe)) {
        $diagnosticsReviewMismatches.Add("review_template.public_safe is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.external_sharing_allowed)) {
        $diagnosticsReviewMismatches.Add("review_template.external_sharing_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.claim_allowed)) {
        $diagnosticsReviewMismatches.Add("review_template.claim_allowed is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_template.required_before_external_sharing)) {
        $diagnosticsReviewMismatches.Add("review_template.required_before_external_sharing is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.review_fields_complete)) {
        $diagnosticsReviewMismatches.Add("review_template.review_fields_complete is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_template.external_sharing_blocked)) {
        $diagnosticsReviewMismatches.Add("review_template.external_sharing_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_template.separate_human_content_review_required)) {
        $diagnosticsReviewMismatches.Add("review_template.separate_human_content_review_required is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.actual_package_content_review_completed)) {
        $diagnosticsReviewMismatches.Add("review_template.actual_package_content_review_completed is not false")
    }
    if ($diagnosticsReviewMismatches.Count -gt 0) {
        $contractFailures.Add("latest diagnostics external-review helper artifact failed fail-closed validation")
    }
    $safeDiagnosticsReviewMarker = if ($diagnosticsReviewMarker -eq "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF") { $diagnosticsReviewMarker } else { "invalid_redacted" }
    $safeReviewStatus = if ($reviewStatus -in $allowedDiagnosticsReviewStatuses) { $reviewStatus } else { "invalid_redacted" }
    [ordered]@{
        found = $true
        path = $latestDiagnosticsReview.path
        last_write_utc = $latestDiagnosticsReview.last_write_utc
        marker = $safeDiagnosticsReviewMarker
        source_contract_status = if ($diagnosticsReviewMismatches.Count -eq 0) {
            if ($reviewStatus -eq "manual_external_review_template_ready") { "valid_not_signoff_template" } else { "valid_fail_closed_template" }
        } else { "source_contract_mismatch" }
        mismatch_reasons = @($diagnosticsReviewMismatches)
        review_status = $safeReviewStatus
        public_safe = $false
        external_sharing_allowed = $false
        claim_allowed = $false
        human_review_signoff = $false
        template_is_human_signoff = $false
        review_fields_complete = $false
        actual_package_content_review_completed = $false
        external_sharing_blocked = $true
        separate_human_content_review_required = $true
        checklist_count = Get-ArrayCount $latestDiagnosticsReview.data.review_template.checklist
    }
}
elseif ($latestDiagnosticsReview.found) {
    $contractFailures.Add("latest diagnostics external-review helper artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestDiagnosticsReview.path
        last_write_utc = $latestDiagnosticsReview.last_write_utc
        parse_error = $latestDiagnosticsReview.error
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $diagnosticsReviewEvidenceRootPath
        review_status = "not_collected_by_this_packet"
        public_safe = $false
        external_sharing_allowed = $false
        claim_allowed = $false
        review_fields_complete = $false
        actual_package_content_review_completed = $false
        external_sharing_blocked = $true
        separate_human_content_review_required = $true
    }
}

$resultQualityReviewNeedles = @(
    "result-quality-review.redacted.json",
    "NOT_RESULT_QUALITY_SIGNOFF",
    'result_quality_signoff = $false',
    'signoff = $false',
    'claim_allowed = $false',
    'completed_result_evidence = $false',
    'result_quality_claim_blocked = $true',
    'separate_human_signoff_required = $true',
    'packet_is_rc_signoff = $false',
    'packet_is_release_signoff = $false',
    "not completed-result evidence",
    "not release sign-off"
)
$resultQualityReviewContract = Get-SourceContract "scripts/collect_result_quality_review_packet.ps1" $resultQualityReviewNeedles
if (-not $resultQualityReviewContract.required_markers_present) {
    $contractFailures.Add("result-quality review helper source contract is missing required non-signoff markers")
}
$latestResultQualityReview = Find-LatestJsonArtifact $resultQualityReviewEvidenceRootPath "result-quality-review.redacted.json"
$resultQualityReviewLatestSummary = if ($latestResultQualityReview.found -and $null -ne $latestResultQualityReview.data) {
    $resultQualityMismatches = New-Object System.Collections.Generic.List[string]
    $resultQualityMarker = [string]$latestResultQualityReview.data.marker
    $resultQualityStatus = [string]$latestResultQualityReview.data.summary.status
    $allowedResultQualityStatuses = @(
        "blocked_missing_fields",
        "blocked_invalid_fields",
        "blocked_reason_recorded",
        "manual_review_fields_recorded_not_signoff"
    )
    if ($resultQualityMarker -ne "NOT_RESULT_QUALITY_SIGNOFF") {
        $resultQualityMismatches.Add("marker is missing or not NOT_RESULT_QUALITY_SIGNOFF")
    }
    if ($resultQualityStatus -notin $allowedResultQualityStatuses) {
        $resultQualityMismatches.Add("summary.status is not an allowed result-quality review status")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.signoff)) {
        $resultQualityMismatches.Add("summary.signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.result_quality_signoff)) {
        $resultQualityMismatches.Add("summary.result_quality_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.claim_allowed)) {
        $resultQualityMismatches.Add("summary.claim_allowed is not false")
    }
    if (-not (Test-JsonTrue $latestResultQualityReview.data.summary.result_quality_claim_blocked)) {
        $resultQualityMismatches.Add("summary.result_quality_claim_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestResultQualityReview.data.summary.separate_human_signoff_required)) {
        $resultQualityMismatches.Add("summary.separate_human_signoff_required is not true")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.completed_result_evidence)) {
        $resultQualityMismatches.Add("summary.completed_result_evidence is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.release_candidate_signoff)) {
        $resultQualityMismatches.Add("summary.release_candidate_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.release_signoff)) {
        $resultQualityMismatches.Add("summary.release_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.template_is_signoff)) {
        $resultQualityMismatches.Add("summary.template_is_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.claim_allowed)) {
        $resultQualityMismatches.Add("claim_controls.claim_allowed is not false")
    }
    if (-not (Test-JsonTrue $latestResultQualityReview.data.claim_controls.result_quality_claim_blocked)) {
        $resultQualityMismatches.Add("claim_controls.result_quality_claim_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestResultQualityReview.data.claim_controls.separate_human_signoff_required)) {
        $resultQualityMismatches.Add("claim_controls.separate_human_signoff_required is not true")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.result_quality_signoff)) {
        $resultQualityMismatches.Add("claim_controls.result_quality_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.completed_result_evidence)) {
        $resultQualityMismatches.Add("claim_controls.completed_result_evidence is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.packet_is_rc_signoff)) {
        $resultQualityMismatches.Add("claim_controls.packet_is_rc_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.packet_is_release_signoff)) {
        $resultQualityMismatches.Add("claim_controls.packet_is_release_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.readonly_scope.starts_product_processes)) {
        $resultQualityMismatches.Add("readonly_scope.starts_product_processes is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.readonly_scope.performs_network_requests)) {
        $resultQualityMismatches.Add("readonly_scope.performs_network_requests is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.readonly_scope.uploads_external_services)) {
        $resultQualityMismatches.Add("readonly_scope.uploads_external_services is not false")
    }
    if (-not (Test-JsonNonNegativeInteger $latestResultQualityReview.data.summary.missing_field_count)) {
        $resultQualityMismatches.Add("summary.missing_field_count is not a non-negative JSON integer")
    }
    if (-not (Test-JsonNonNegativeInteger $latestResultQualityReview.data.summary.issue_count)) {
        $resultQualityMismatches.Add("summary.issue_count is not a non-negative JSON integer")
    }
    $expectedReviewFieldsComplete = [bool](
        $resultQualityStatus -eq "manual_review_fields_recorded_not_signoff" -and
        (Test-JsonNonNegativeInteger $latestResultQualityReview.data.summary.missing_field_count) -and
        (Test-JsonNonNegativeInteger $latestResultQualityReview.data.summary.issue_count) -and
        [int64]$latestResultQualityReview.data.summary.missing_field_count -eq 0 -and
        [int64]$latestResultQualityReview.data.summary.issue_count -eq 0
    )
    if ($latestResultQualityReview.data.summary.review_fields_complete -isnot [bool]) {
        $resultQualityMismatches.Add("summary.review_fields_complete is not a JSON boolean")
    }
    elseif ([bool]$latestResultQualityReview.data.summary.review_fields_complete -ne $expectedReviewFieldsComplete) {
        $resultQualityMismatches.Add("summary.review_fields_complete does not match missing/issue/status state")
    }
    if ($resultQualityMismatches.Count -gt 0) {
        $contractFailures.Add("latest result-quality review helper artifact failed fail-closed validation")
    }
    [ordered]@{
        found = $true
        path = $latestResultQualityReview.path
        last_write_utc = $latestResultQualityReview.last_write_utc
        marker = if ($resultQualityMarker -eq "NOT_RESULT_QUALITY_SIGNOFF") { $resultQualityMarker } else { "invalid_redacted" }
        source_contract_status = if ($resultQualityMismatches.Count -eq 0) { "valid_not_signoff_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($resultQualityMismatches)
        review_status = if ($resultQualityStatus -in $allowedResultQualityStatuses) { $resultQualityStatus } else { "invalid_redacted" }
        review_fields_complete = [bool]($latestResultQualityReview.data.summary.review_fields_complete -is [bool] -and [bool]$latestResultQualityReview.data.summary.review_fields_complete -and $resultQualityMismatches.Count -eq 0)
        missing_field_count = Get-StrictJsonNonNegativeIntegerOrZero $latestResultQualityReview.data.summary.missing_field_count
        issue_count = Get-StrictJsonNonNegativeIntegerOrZero $latestResultQualityReview.data.summary.issue_count
        blocked_reason_count = Get-ArrayCount $latestResultQualityReview.data.reviewer.blocked_reason_redacted
        observed_artifact_count = Get-ArrayCount $latestResultQualityReview.data.task_result_artifact.observed_artifacts_redacted
        result_quality_signoff = $false
        result_quality_claim_blocked = $true
        separate_human_signoff_required = $true
        signoff = $false
        claim_allowed = $false
        completed_result_evidence = $false
        release_candidate_signoff = $false
        release_signoff = $false
    }
}
elseif ($latestResultQualityReview.found) {
    $contractFailures.Add("latest result-quality review helper artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestResultQualityReview.path
        last_write_utc = $latestResultQualityReview.last_write_utc
        parse_error = $latestResultQualityReview.error
        result_quality_signoff = $false
        signoff = $false
        claim_allowed = $false
        completed_result_evidence = $false
        release_candidate_signoff = $false
        release_signoff = $false
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $resultQualityReviewEvidenceRootPath
        review_status = "not_collected_by_this_packet"
        result_quality_signoff = $false
        signoff = $false
        claim_allowed = $false
        completed_result_evidence = $false
        release_candidate_signoff = $false
        release_signoff = $false
    }
}

$rcHandoffNeedles = @(
    "rc-handoff-template.redacted.json",
    "NOT_RELEASE_CANDIDATE_SIGNOFF",
    'release_candidate_signoff = $false',
    'claim_allowed = $false',
    'template_is_rc_pass = $false',
    'template_is_release_signoff = $false',
    'gate_commands_run_by_this_helper = $false',
    'must_not_tag_publish_or_announce = $true',
    "Do not tag, publish, announce, ship, or call the candidate passed from this template"
)
$rcHandoffContract = Get-SourceContract "scripts/collect_rc_handoff_template.ps1" $rcHandoffNeedles
if (-not $rcHandoffContract.required_markers_present) {
    $contractFailures.Add("RC handoff template source contract is missing required non-signoff markers")
}

$latestRcHandoff = Find-LatestJsonArtifact $rcHandoffEvidenceRootPath "rc-handoff-template.redacted.json"
$rcHandoffLatestSummary = if ($latestRcHandoff.found -and $null -ne $latestRcHandoff.data) {
    $rcHandoffMismatches = New-Object System.Collections.Generic.List[string]
    $rcMarker = [string]$latestRcHandoff.data.marker
    $rcStatus = [string]$latestRcHandoff.data.summary.status
    $rcMissingRequiredFieldsCount = Get-ArrayCount $latestRcHandoff.data.summary.missing_required_fields
    $allowedRcStatuses = @(
        "manual_rc_handoff_required",
        "manual_rc_handoff_recorded_unverified_by_this_helper"
    )
    $rcRequiredFields = @(
        "candidate.commit_or_build_id",
        "candidate.platform",
        "artifact_labels",
        "gate_results.commands_and_exits",
        "strict_state_source",
        "manual_p1_checks",
        "waivers",
        "residual_risks"
    )
    $rcArtifactLabelCount = Get-ArrayCount $latestRcHandoff.data.artifacts.labels
    $rcGateEntryCount = Get-ArrayCount $latestRcHandoff.data.gate_results.entries
    $rcManualP1CheckCount = Get-ArrayCount $latestRcHandoff.data.manual_p1_checks.entries
    $rcWaiverCount = Get-ArrayCount $latestRcHandoff.data.waivers.entries
    $rcResidualRiskCount = Get-ArrayCount $latestRcHandoff.data.residual_risks.entries
    $rcCandidateCommitOrBuildRecorded = (Test-MeaningfulEvidenceValue ([string]$latestRcHandoff.data.candidate.commit)) -or (Test-MeaningfulEvidenceValue ([string]$latestRcHandoff.data.candidate.build_id))
    $rcPlatformRecorded = Test-MeaningfulEvidenceValue ([string]$latestRcHandoff.data.candidate.platform)
    $rcStrictStateRecorded = Test-MeaningfulEvidenceValue ([string]$latestRcHandoff.data.strict_state_source.source)

    if (-not (Test-JsonIntegerOne $latestRcHandoff.data.schema_version)) {
        $rcHandoffMismatches.Add("schema_version is not 1")
    }
    if ([string]$latestRcHandoff.data.generated_by -ne "scripts/collect_rc_handoff_template.ps1") {
        $rcHandoffMismatches.Add("generated_by is not scripts/collect_rc_handoff_template.ps1")
    }
    if ($rcMarker -ne "NOT_RELEASE_CANDIDATE_SIGNOFF") {
        $rcHandoffMismatches.Add("marker is missing or not NOT_RELEASE_CANDIDATE_SIGNOFF")
    }
    if ($rcStatus -notin $allowedRcStatuses) {
        $rcHandoffMismatches.Add("summary.status is not a recognized non-signoff RC handoff status")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.release_candidate_signoff)) {
        $rcHandoffMismatches.Add("summary.release_candidate_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.claim_allowed)) {
        $rcHandoffMismatches.Add("summary.claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.template_is_rc_pass)) {
        $rcHandoffMismatches.Add("summary.template_is_rc_pass is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.template_is_release_signoff)) {
        $rcHandoffMismatches.Add("summary.template_is_release_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.gate_commands_run_by_this_helper)) {
        $rcHandoffMismatches.Add("summary.gate_commands_run_by_this_helper is not false")
    }
    if (-not (Test-JsonNonNegativeInteger $latestRcHandoff.data.summary.missing_required_fields_count)) {
        $rcHandoffMismatches.Add("summary.missing_required_fields_count is not a non-negative JSON integer")
    }
    elseif ([int64]$latestRcHandoff.data.summary.missing_required_fields_count -ne [int64]$rcMissingRequiredFieldsCount) {
        $rcHandoffMismatches.Add("summary.missing_required_fields_count does not match missing_required_fields")
    }
    if ($rcStatus -eq "manual_rc_handoff_recorded_unverified_by_this_helper" -and $rcMissingRequiredFieldsCount -ne 0) {
        $rcHandoffMismatches.Add("recorded RC handoff status still has missing required fields")
    }
    if ($rcStatus -eq "manual_rc_handoff_required" -and $rcMissingRequiredFieldsCount -eq 0) {
        $rcHandoffMismatches.Add("required RC handoff status has no missing required fields")
    }
    foreach ($requiredField in $rcRequiredFields) {
        if (-not (Test-ArrayContainsText $latestRcHandoff.data.required_fields $requiredField)) {
            $rcHandoffMismatches.Add("required_fields is missing $requiredField")
        }
    }
    if ($rcStatus -eq "manual_rc_handoff_recorded_unverified_by_this_helper") {
        if (-not $rcCandidateCommitOrBuildRecorded) {
            $rcHandoffMismatches.Add("recorded RC handoff is missing candidate commit or build id")
        }
        if ([string]$latestRcHandoff.data.candidate.commit_or_build_id_status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("candidate.commit_or_build_id_status is not recorded")
        }
        if (-not $rcPlatformRecorded) {
            $rcHandoffMismatches.Add("recorded RC handoff is missing candidate platform")
        }
        if ([string]$latestRcHandoff.data.candidate.platform_status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("candidate.platform_status is not recorded")
        }
        if ($rcArtifactLabelCount -eq 0 -or [string]$latestRcHandoff.data.artifacts.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing recorded artifact labels")
        }
        if ($rcGateEntryCount -eq 0 -or [string]$latestRcHandoff.data.gate_results.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing recorded gate command/exit entries")
        }
        if (-not (Test-JsonTrue $latestRcHandoff.data.gate_results.commands_and_exits_count_match)) {
            $rcHandoffMismatches.Add("recorded RC handoff gate command/exit counts do not match")
        }
        if (-not $rcStrictStateRecorded -or [string]$latestRcHandoff.data.strict_state_source.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing strict-state source")
        }
        if ($rcManualP1CheckCount -eq 0 -or [string]$latestRcHandoff.data.manual_p1_checks.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing manual P1 checks")
        }
        if ($rcWaiverCount -eq 0 -or [string]$latestRcHandoff.data.waivers.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing waiver record")
        }
        if ($rcResidualRiskCount -eq 0 -or [string]$latestRcHandoff.data.residual_risks.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing residual risk record")
        }
        foreach ($entry in @($latestRcHandoff.data.artifacts.labels)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.value))) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an empty artifact label")
                break
            }
        }
        foreach ($entry in @($latestRcHandoff.data.gate_results.entries)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.command)) -or -not (Test-RcGateExitValue ([string]$entry.exit_status)) -or -not (Test-JsonTrue $entry.exact_command_and_exit_recorded)) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an incomplete gate command/exit entry")
                break
            }
        }
        foreach ($entry in @($latestRcHandoff.data.manual_p1_checks.entries)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.value))) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an empty manual P1 check")
                break
            }
        }
        foreach ($entry in @($latestRcHandoff.data.waivers.entries)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.value))) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an empty waiver record")
                break
            }
        }
        foreach ($entry in @($latestRcHandoff.data.residual_risks.entries)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.value))) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an empty residual risk")
                break
            }
        }
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.signoff_controls.release_candidate_signoff)) {
        $rcHandoffMismatches.Add("signoff_controls.release_candidate_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.signoff_controls.claim_allowed)) {
        $rcHandoffMismatches.Add("signoff_controls.claim_allowed is not false")
    }
    if (-not (Test-JsonTrue $latestRcHandoff.data.signoff_controls.pass_defaults_remain_false)) {
        $rcHandoffMismatches.Add("signoff_controls.pass_defaults_remain_false is not true")
    }
    if (-not (Test-JsonTrue $latestRcHandoff.data.signoff_controls.must_not_tag_publish_or_announce)) {
        $rcHandoffMismatches.Add("signoff_controls.must_not_tag_publish_or_announce is not true")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.readonly_scope.starts_product_processes)) {
        $rcHandoffMismatches.Add("readonly_scope.starts_product_processes is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.readonly_scope.runs_release_commands)) {
        $rcHandoffMismatches.Add("readonly_scope.runs_release_commands is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.readonly_scope.performs_network_requests)) {
        $rcHandoffMismatches.Add("readonly_scope.performs_network_requests is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.readonly_scope.installs_dependencies)) {
        $rcHandoffMismatches.Add("readonly_scope.installs_dependencies is not false")
    }
    if (-not (Test-JsonTrue $latestRcHandoff.data.readonly_scope.writes_only_rc_handoff_template_artifacts)) {
        $rcHandoffMismatches.Add("readonly_scope.writes_only_rc_handoff_template_artifacts is not true")
    }
    if (-not (Test-JsonTrue $latestRcHandoff.data.gate_results.exact_commands_and_exits_required)) {
        $rcHandoffMismatches.Add("gate_results.exact_commands_and_exits_required is not true")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.gate_results.commands_run_by_this_helper)) {
        $rcHandoffMismatches.Add("gate_results.commands_run_by_this_helper is not false")
    }
    foreach ($entry in @($latestRcHandoff.data.gate_results.entries)) {
        if (-not (Test-JsonFalse $entry.pass_verified_by_this_helper)) {
            $rcHandoffMismatches.Add("gate_results.entries.pass_verified_by_this_helper is not false")
            break
        }
    }
    if (-not (Test-ArrayContainsText $latestRcHandoff.data.must_not_be_recorded_as "release-candidate pass")) {
        $rcHandoffMismatches.Add("must_not_be_recorded_as is missing release-candidate pass")
    }
    if (-not (Test-ArrayContainsText $latestRcHandoff.data.must_not_be_recorded_as "release sign-off")) {
        $rcHandoffMismatches.Add("must_not_be_recorded_as is missing release sign-off")
    }
    if (-not (Test-ArrayContainsText $latestRcHandoff.data.must_not_be_recorded_as "permission to tag, publish, announce, or ship")) {
        $rcHandoffMismatches.Add("must_not_be_recorded_as is missing permission-to-ship warning")
    }

    if ($rcHandoffMismatches.Count -gt 0) {
        $contractFailures.Add("latest RC handoff helper artifact failed fail-closed validation")
    }

    [ordered]@{
        found = $true
        path = $latestRcHandoff.path
        last_write_utc = $latestRcHandoff.last_write_utc
        marker = if ($rcMarker -eq "NOT_RELEASE_CANDIDATE_SIGNOFF") { $rcMarker } else { "invalid_redacted" }
        source_contract_status = if ($rcHandoffMismatches.Count -eq 0) { "valid_not_signoff_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($rcHandoffMismatches)
        handoff_status = if ($rcStatus -in $allowedRcStatuses) { $rcStatus } else { "invalid_redacted" }
        release_candidate_signoff = $false
        claim_allowed = $false
        template_is_rc_pass = $false
        template_is_release_signoff = $false
        gate_commands_run_by_this_helper = $false
        must_not_tag_publish_or_announce = $true
        missing_required_fields_count = Get-StrictJsonNonNegativeIntegerOrZero $latestRcHandoff.data.summary.missing_required_fields_count
        missing_required_fields = @($latestRcHandoff.data.summary.missing_required_fields)
        required_fields_recorded = [bool]($rcStatus -eq "manual_rc_handoff_recorded_unverified_by_this_helper" -and $rcMissingRequiredFieldsCount -eq 0 -and $rcHandoffMismatches.Count -eq 0)
        artifact_label_count = $rcArtifactLabelCount
        gate_result_count = $rcGateEntryCount
        manual_p1_check_count = $rcManualP1CheckCount
        waiver_count = $rcWaiverCount
        residual_risk_count = $rcResidualRiskCount
        candidate = [ordered]@{
            commit = Redact-TextValue ([string]$latestRcHandoff.data.candidate.commit)
            build_id = Redact-TextValue ([string]$latestRcHandoff.data.candidate.build_id)
            platform = Redact-TextValue ([string]$latestRcHandoff.data.candidate.platform)
            commit_or_build_id_status = Redact-TextValue ([string]$latestRcHandoff.data.candidate.commit_or_build_id_status)
            platform_status = Redact-TextValue ([string]$latestRcHandoff.data.candidate.platform_status)
        }
    }
}
elseif ($latestRcHandoff.found) {
    $contractFailures.Add("latest RC handoff helper artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestRcHandoff.path
        last_write_utc = $latestRcHandoff.last_write_utc
        parse_error = $latestRcHandoff.error
        source_contract_status = "parse_error"
        release_candidate_signoff = $false
        claim_allowed = $false
        template_is_rc_pass = $false
        template_is_release_signoff = $false
        gate_commands_run_by_this_helper = $false
        must_not_tag_publish_or_announce = $true
        required_fields_recorded = $false
        artifact_label_count = 0
        gate_result_count = 0
        manual_p1_check_count = 0
        waiver_count = 0
        residual_risk_count = 0
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $rcHandoffEvidenceRootPath
        source_contract_status = "not_collected_by_this_packet"
        handoff_status = "not_collected_by_this_packet"
        release_candidate_signoff = $false
        claim_allowed = $false
        template_is_rc_pass = $false
        template_is_release_signoff = $false
        gate_commands_run_by_this_helper = $false
        must_not_tag_publish_or_announce = $true
        missing_required_fields_count = 0
        missing_required_fields = @()
        required_fields_recorded = $false
        artifact_label_count = 0
        gate_result_count = 0
        manual_p1_check_count = 0
        waiver_count = 0
        residual_risk_count = 0
    }
}

$settingsNeedles = @(
    "settings local model experience smoke passed; screenshots:",
    "assertCleanMachineSetupPlanContract",
    "clean machine setup plan must not report local model readiness",
    "clean machine verification should not expose local paths",
    "counters.installRequests",
    "settings-local-model-experience-smoke-desktop.png",
    "settings-local-model-experience-smoke-desktop-setup.png",
    "settings-local-model-experience-smoke-narrow.png",
    "settings-local-model-experience-smoke-narrow-setup.png"
)
$settingsContract = Get-SourceContract "desktop/scripts/settings-local-model-experience-smoke.cjs" $settingsNeedles
if (-not $settingsContract.required_markers_present) {
    $contractFailures.Add("Settings local-model smoke source contract is missing required markers")
}

$settingsArtifactNames = @(
    "settings-local-model-experience-smoke-desktop.png",
    "settings-local-model-experience-smoke-desktop-setup.png",
    "settings-local-model-experience-smoke-narrow.png",
    "settings-local-model-experience-smoke-narrow-setup.png"
)
$settingsArtifacts = @(
    foreach ($name in $settingsArtifactNames) {
        $artifactPath = Join-Path $qaEvidenceRootPath $name
        if (Test-Path -LiteralPath $artifactPath) {
            $item = Get-Item -LiteralPath $artifactPath
            [ordered]@{
                name = $name
                path = Get-DisplayPath $artifactPath
                exists = $true
                bytes = [int64]$item.Length
                last_write_utc = $item.LastWriteTimeUtc.ToString("o")
            }
        }
        else {
            [ordered]@{
                name = $name
                path = Get-DisplayPath $artifactPath
                exists = $false
                bytes = 0
                last_write_utc = ""
            }
        }
    }
)
$settingsArtifactsPresent = @($settingsArtifacts | Where-Object { $_.exists }).Count

$releaseReadinessBlockers = @(
    [ordered]@{
        id = "clean_machine_local_model"
        status = "missing_manual_evidence"
        claim_allowed = $false
        support_evidence = "local-model template, Settings smoke, and Ollama contract counts only"
        required_evidence = "candidate artifact/build/profile plus clean-machine install/start/pull/task-smoke outcome, runtime/model/version, or exact blocked reason"
        beginner_next_step = "Run npm run evidence:local-model-template with the candidate artifact/build/profile and reviewed install/start/pull/task-smoke notes."
        must_not_claim = "clean-machine local/offline model readiness"
    }
    [ordered]@{
        id = "mobile_real_device_lan_wss"
        status = "missing_real_device_artifacts"
        claim_allowed = $false
        support_evidence = "mobile LAN/WSS preflight, backend authorization tests, and mobile client smokes only"
        required_evidence = "real phone/emulator camera QR, actual HTTPS/WSS approval/remote screen/remote input session, certificate trust path, revoke/expiry screenshots or notes"
        beginner_next_step = "Run npm run evidence:mobile-lan-wss first, then attach reviewed phone/emulator artifacts to the generated checklist."
        must_not_claim = "real-device mobile LAN/WSS pass"
    }
    [ordered]@{
        id = "android_installable_remote_control"
        status = if ($androidReleaseGateLatestSummary.release_ready) { "recorded_by_android_release_gate" } else { "missing_apk_or_real_device_gate" }
        claim_allowed = [bool]$androidReleaseGateLatestSummary.claim_controls.installable_android_app_claim_allowed -and [bool]$androidReleaseGateLatestSummary.claim_controls.real_device_remote_control_claim_allowed
        support_evidence = "Android real-device fail-closed template, Android release gate preflight, EAS profile config, and mobile client smokes only"
        required_evidence = "installable QA APK path/hash plus reviewed Android/emulator HTTPS/WSS remote-control evidence JSON backed by QR, trust, screen, input, revoke, expiry, and redaction artifacts"
        beginner_next_step = "Run npm run evidence:android-real-device-template, run npm run android:release-gate -- -PreflightOnly, build the preview APK, then rerun the strict gate with -ArtifactPath and -RealDeviceEvidencePath."
        must_not_claim = "installable Android app or real-device Android remote-control pass"
    }
    [ordered]@{
        id = "natural_language_result_quality"
        status = "missing_result_quality_signoff"
        claim_allowed = $false
        support_evidence = "portable command-dock submission plus explain completion_evidence/result_quality fields only"
        required_evidence = "reviewed user-visible result, source/artifact check, next-step/actionability check, and explicit result-quality sign-off"
        beginner_next_step = "Use the portable smoke result as routing evidence, then manually review the visible task result before claiming quality."
        must_not_claim = "natural-language result-quality sign-off"
    }
    [ordered]@{
        id = "diagnostics_external_public_safety"
        status = "manual_content_review_required"
        claim_allowed = $false
        support_evidence = "diagnostics export contract tests and external-review packet template only"
        required_evidence = "human review of the actual exported diagnostics package contents before any external sharing"
        beginner_next_step = "Export a disposable diagnostics package, review the actual contents, and keep public_safe=false unless a separate policy changes."
        must_not_claim = "public-safe diagnostics approval"
    }
    [ordered]@{
        id = "release_candidate_handoff"
        status = "manual_rc_handoff_required"
        claim_allowed = $false
        support_evidence = "redacted evidence index only"
        required_evidence = "candidate commit/build id, platform/artifact labels, exact gate commands and exits, manual P1 checks, waivers, residual risks"
        beginner_next_step = "Fill rc_handoff_requirements in a separate release handoff before tagging, publishing, or announcing an RC."
        must_not_claim = "release-candidate pass"
    }
)

$packet = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    generated_by = "scripts/collect_release_evidence_packet.ps1"
    outputs = [ordered]@{
        redacted_json = Get-DisplayPath $jsonPath
        redacted_markdown = Get-DisplayPath $markdownPath
    }
    readonly_scope = [ordered]@{
        starts_product_processes = $false
        performs_network_requests = $false
        changes_backend_product_logic = $false
        changes_desktop_ui = $false
        changes_mobile_app = $false
        writes_only_packet_summary_artifacts = $true
    }
    redaction = [ordered]@{
        path_policy = "workspace-relative paths or file labels only"
        raw_logs_included = $false
        source_artifacts_read_for_summary = $true
        secrets_or_tokens_emitted = $false
        mobile_hosts = "uses existing redacted labels only"
    }
    summary = [ordered]@{
        automated_evidence_items = 12
        indexed_evidence_buckets = 12
        evidence_count_is_not_acceptance_count = $true
        source_contract_failures = $contractFailures.Count
        packet_is_pass = $false
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        real_device_signoff = $false
        release_candidate_signoff = $false
        agent_task_completion_signoff = $false
        result_quality_signoff = $false
        diagnostics_public_safe = $false
        release_ready = $false
        claimable_release_signoff = $false
        release_readiness_blocker_count = $releaseReadinessBlockers.Count
        portable_natural_language_scope = "submission_plus_read_only_routing_evidence_only"
        packet_status = if ($contractFailures.Count -eq 0) { "redacted_partial_evidence_summary" } else { "source_contract_failure" }
    }
    evidence = [ordered]@{
        mobile_lan_wss_preflight = [ordered]@{
            status = if ($mobileContract.required_markers_present) { "entry_available" } else { "source_contract_missing" }
            source_contract = $mobileContract
            latest_redacted_summary = $mobileLatestSummary
            automated_scope = "static preflight contract plus latest redacted summary if present"
            not_signoff = @(
                "not a phone/emulator run",
                "not camera or QR scan evidence",
                "not an actual WSS connection",
                "not Android/emulator certificate trust evidence",
                "not real-device pass evidence"
            )
        }
        android_release_gate = [ordered]@{
            status = if ($androidReleaseGateContract.required_markers_present) { "entry_available" } else { "source_contract_missing" }
            source_contract = $androidReleaseGateContract
            latest_redacted_summary = $androidReleaseGateLatestSummary
            expected_preflight_status = "preflight_ready_not_release"
            expected_strict_status = "passed"
            expected_packet_creates_apk_or_real_device_pass = $false
            automated_scope = "source/config preflight plus strict gate result when APK and real-device evidence are supplied"
            not_signoff = @(
                "preflight is not an APK build",
                "preflight is not an install on Android",
                "preflight is not phone/emulator WSS evidence",
                "strict gate remains blocked without installable APK and reviewed real-device evidence"
            )
        }
        android_real_device_evidence_template = [ordered]@{
            status = if ($androidRealDeviceTemplateContract.required_markers_present) { "fail_closed_template_contract_present" } else { "source_contract_missing" }
            source_contract = $androidRealDeviceTemplateContract
            latest_redacted_template = $androidRealDeviceTemplateLatestSummary
            automated_scope = "template/source contract and latest fail-closed redacted template if present"
            expected_template_status = "manual_real_device_evidence_required"
            expected_real_device_result = "uncollected"
            expected_pass_claim_allowed = $false
            not_signoff = @(
                "template is not a phone/emulator run",
                "template is not camera QR evidence",
                "template is not HTTPS/WSS or certificate-trust evidence",
                "template is not remote screen/input/revoke/expiry evidence",
                "template is not a real-device pass until reviewed artifacts fill every required check"
            )
        }
        mobile_remote_input_active_grant_contract = [ordered]@{
            status = if (($mobileRemoteInputContracts | Where-Object { -not $_.required_markers_present }).Count -eq 0) { "fail_closed_source_contract_present" } else { "source_contract_missing" }
            source_contracts = $mobileRemoteInputContracts
            automated_scope = "static source contract markers in mobile UI/client/smoke sources"
            verify_command = "npm --prefix mobile run smoke:remote-input-grant"
            latest_execution_status = "not_run_by_this_packet"
            not_signoff = @(
                "not evidence that the smoke command was executed by this packet",
                "not a real phone/emulator run",
                "not proof of a live desktop-to-mobile remote input session",
                "not backend TestClient, desktop smoke, packaged, or clean-machine evidence by itself",
                "not actual WSS network evidence",
                "not release-candidate sign-off"
            )
        }
        portable_first_screen_smoke = [ordered]@{
            status = if ($portableContract.required_markers_present) { "limited_evidence_contract_present" } else { "source_contract_missing" }
            source_contract = $portableContract
            latest_redacted_status_log = $portableLatestSummary
            automated_scope = "latest redacted portable.status.log summary if present"
            verify_command = "npm run smoke:portable-first-screen"
            not_signoff = @(
                "not clean-machine release-candidate install validation",
                "not completed task-result sign-off",
                "not release-candidate sign-off",
                "not full natural-language agent task completion",
                "not platform distribution evidence"
            )
        }
        ollama_local_model_contracts = [ordered]@{
            status = if ($ollamaContractCount -gt 0) { "contract_count_available" } else { "contract_count_missing" }
            contract_count = $ollamaContractCount
            files = $ollamaCounts
            verify_command = "python -m pytest backend/tests/test_ollama_service.py backend/tests/test_ollama_install_endpoint.py -q"
            latest_execution_status = "not_run_by_this_packet"
            not_signoff = @(
                "not clean-machine local model install evidence",
                "not packaged-profile local model start evidence",
                "not proof that a model was pulled or listed by a real Ollama service"
            )
        }
        local_model_clean_machine_template = [ordered]@{
            status = if ($localModelTemplateContract.required_markers_present) { "manual_clean_machine_template_contract_present" } else { "source_contract_missing" }
            source_contract = $localModelTemplateContract
            latest_redacted_clean_machine_template = $localModelTemplateLatestSummary
            expected_marker = "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
            expected_clean_machine_signoff = $false
            expected_install_start_pull_pass = $false
            expected_task_smoke_pass = $false
            verify_command = "python -m pytest backend/tests/test_start_app_script.py -q"
            not_signoff = @(
                "not true local model install pass",
                "not true local model start pass",
                "not true local model pull pass",
                "not true local model task-smoke pass",
                "not clean-machine local-model readiness",
                "template/dev smoke must not be recorded as clean-machine pass",
                "not release-candidate sign-off"
            )
        }
        diagnostics_external_review = [ordered]@{
            status = if ($diagnosticsContract.required_markers_present) { "manual_review_required_contract_present" } else { "source_contract_missing" }
            source_contract = $diagnosticsContract
            latest_redacted_review_packet = $diagnosticsReviewLatestSummary
            expected_external_review_status = "manual_review_required"
            expected_required_before_external_sharing = $true
            expected_public_safe = $false
            expected_claim_allowed = $false
            expected_actual_package_content_review_completed = $false
            expected_external_sharing_blocked = $true
            expected_separate_human_content_review_required = $true
            verify_command = "python -m pytest backend/tests/test_system_diagnostics.py -q"
            not_signoff = @(
                "not external public-safety approval",
                "not permission to share diagnostics outside trusted support",
                "not clean-machine diagnostics sign-off",
                "not a human content review sign-off"
            )
        }
        result_quality_review = [ordered]@{
            status = if ($resultQualityReviewContract.required_markers_present) { "manual_review_required_contract_present" } else { "source_contract_missing" }
            source_contract = $resultQualityReviewContract
            latest_redacted_review_packet = $resultQualityReviewLatestSummary
            expected_marker = "NOT_RESULT_QUALITY_SIGNOFF"
            expected_result_quality_signoff = $false
            expected_claim_allowed = $false
            expected_completed_result_evidence = $false
            verify_command = "python -m pytest backend/tests/test_result_quality_review_packet.py -q"
            not_signoff = @(
                "not completed-result evidence",
                "not natural-language result-quality sign-off",
                "not Task Workspace sign-off",
                "not release-candidate sign-off",
                "not release sign-off"
            )
        }
        rc_handoff_template = [ordered]@{
            status = if ($rcHandoffContract.required_markers_present) { "manual_rc_handoff_contract_present" } else { "source_contract_missing" }
            source_contract = $rcHandoffContract
            latest_redacted_handoff_template = $rcHandoffLatestSummary
            expected_marker = "NOT_RELEASE_CANDIDATE_SIGNOFF"
            expected_release_candidate_signoff = $false
            expected_claim_allowed = $false
            expected_gate_commands_run_by_this_helper = $false
            verify_command = "python -m pytest backend/tests/test_start_app_script.py -q"
            not_signoff = @(
                "not release-candidate pass",
                "not release-candidate sign-off",
                "not release sign-off",
                "not proof that release gates were run",
                "not permission to tag, publish, announce, or ship",
                "not waiver approval",
                "not manual P1 review approval"
            )
        }
        settings_local_model_smoke = [ordered]@{
            status = if ($settingsContract.required_markers_present -and $settingsArtifactsPresent -eq $settingsArtifactNames.Count) { "source_contract_and_artifacts_present" } elseif ($settingsContract.required_markers_present) { "source_contract_present_artifacts_incomplete" } else { "source_contract_missing" }
            source_contract = $settingsContract
            expected_artifact_count = $settingsArtifactNames.Count
            present_artifact_count = $settingsArtifactsPresent
            artifacts = $settingsArtifacts
            verify_command = "npm --prefix desktop run smoke:settings-local-model"
            latest_execution_status = "not_run_by_this_packet"
            not_signoff = @(
                "not clean-machine local-model readiness",
                "not packaged Settings evidence",
                "not release-candidate layout sign-off",
                "not true local model install/start/pull evidence"
            )
        }
    }
    not_clean_machine_or_signoff = @(
        "This packet summarizes automatically checkable source contracts and existing redacted artifacts only.",
        "It does not create clean-machine install/start/pull local-model evidence.",
        "It does not create clean-machine local-model task-smoke pass evidence.",
        "It does not turn the local-model template or Settings dev smoke into clean-machine pass evidence.",
        "It does not create real phone/emulator camera/QR/WSS/certificate-trust evidence.",
        "It does not create installable Android APK pass or real-device Android remote-control pass; Android template/gate entries are indexed redacted evidence only.",
        "It does not create natural-language result-quality sign-off; result_quality_signoff remains false pending separate human sign-off.",
        "It does not make diagnostics packages public-safe; public_safe remains false pending manual external review.",
        "It is not release-candidate sign-off without the release gate command results, candidate id, artifact paths, and manual P1 sign-off."
    )
    rc_handoff_requirements = [ordered]@{
        status = "manual_rc_handoff_required"
        release_candidate_signoff = $false
        packet_is_rc_signoff = $false
        required_before_rc_signoff = @(
            "candidate commit or build id",
            "platform and packaged artifact paths or redacted artifact labels",
            "exact release gate commands and full exit status",
            "strict-state-machine source used for the release gate",
            "manual P1 checks with owner and timestamp",
            "waivers with owner, reason, expiry condition, and follow-up task",
            "residual risks"
        )
        missing_by_default = @(
            "candidate commit or build id",
            "platform and packaged artifact paths or redacted artifact labels",
            "exact release gate commands and full exit status",
            "strict-state-machine source used for the release gate",
            "manual P1 checks",
            "waivers",
            "residual risks"
        )
        must_not_be_recorded_as = @(
            "release-candidate pass",
            "release sign-off",
            "clean-machine pass",
            "real-device pass",
            "public-safe diagnostics approval",
            "completed task-result sign-off"
        )
        beginner_instruction = "Use this packet as a redacted checklist only; do not tag, publish, or announce an RC until a separate handoff fills every required field."
    }
    release_readiness_blockers = $releaseReadinessBlockers
    next_manual_evidence_needed = @(
        "Clean-machine or packaged-profile local model install/start/pull/task-smoke evidence when local/offline model readiness is claimed.",
        "Real phone/emulator camera or QR pairing path, actual WSS connection, and explicit device certificate trust evidence when mobile LAN/WSS readiness is claimed.",
        "Installable Android QA APK path/hash plus filled reviewed Android real-device evidence JSON and strict Android release gate evidence before claiming Android app or Android remote-control readiness.",
        "Actual natural-language result-quality human sign-off after reviewing the user-visible result, source/artifact labels, and next-step actionability.",
        "Manual diagnostics package review before any external sharing.",
        "Release-candidate artifact verification and manual P1 sign-off before RC approval."
    )
}

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# Release Evidence Packet Summary")
$markdownLines.Add("")
$markdownLines.Add("- Generated: $($packet.generated_at_utc)")
$markdownLines.Add("- JSON: $($packet.outputs.redacted_json)")
$markdownLines.Add("- Markdown: $($packet.outputs.redacted_markdown)")
$markdownLines.Add("- Status: $($packet.summary.packet_status)")
$markdownLines.Add("- Packet role: evidence index only; packet_is_pass=false; evidence bucket count is not an acceptance count.")
$markdownLines.Add("- Release readiness: release_ready=$($packet.summary.release_ready); claimable_release_signoff=$($packet.summary.claimable_release_signoff); blocker_count=$($packet.summary.release_readiness_blocker_count).")
$markdownLines.Add("- Scope: no product process starts, no network requests, no backend/desktop/mobile product changes.")
$markdownLines.Add("")
$markdownLines.Add("## Not Sign-Off")
foreach ($item in $packet.not_clean_machine_or_signoff) {
    $markdownLines.Add("- $item")
}
$markdownLines.Add("")
$rcHandoff = $packet.rc_handoff_requirements
$latestRcHandoffTemplate = $packet.evidence.rc_handoff_template.latest_redacted_handoff_template
$markdownLines.Add("## RC Handoff Requirements")
$markdownLines.Add("- Status: $($rcHandoff.status); release_candidate_signoff=$($rcHandoff.release_candidate_signoff); packet_is_rc_signoff=$($rcHandoff.packet_is_rc_signoff).")
$markdownLines.Add("- Beginner instruction: $($rcHandoff.beginner_instruction)")
$markdownLines.Add("- Latest RC handoff template: found=$($latestRcHandoffTemplate.found), status=$($latestRcHandoffTemplate.handoff_status), source_contract=$($latestRcHandoffTemplate.source_contract_status), missing_required_fields=$($latestRcHandoffTemplate.missing_required_fields_count), required_fields_recorded=$($latestRcHandoffTemplate.required_fields_recorded), release_candidate_signoff=$($latestRcHandoffTemplate.release_candidate_signoff), claim_allowed=$($latestRcHandoffTemplate.claim_allowed).")
$markdownLines.Add("- Latest RC handoff counts: artifacts=$($latestRcHandoffTemplate.artifact_label_count), gate_entries=$($latestRcHandoffTemplate.gate_result_count), manual_p1_checks=$($latestRcHandoffTemplate.manual_p1_check_count), waivers=$($latestRcHandoffTemplate.waiver_count), residual_risks=$($latestRcHandoffTemplate.residual_risk_count); commands_run_by_helper=$($latestRcHandoffTemplate.gate_commands_run_by_this_helper).")
$markdownLines.Add("- Required before RC sign-off:")
foreach ($item in $rcHandoff.required_before_rc_signoff) {
    $markdownLines.Add("  - $item")
}
$markdownLines.Add("- Must not be recorded as:")
foreach ($item in $rcHandoff.must_not_be_recorded_as) {
    $markdownLines.Add("  - $item")
}
$markdownLines.Add("")
$markdownLines.Add("## Release Readiness Blockers")
foreach ($blocker in $packet.release_readiness_blockers) {
    $markdownLines.Add("- $($blocker.id): status=$($blocker.status); claim_allowed=$($blocker.claim_allowed); required=$($blocker.required_evidence); next=$($blocker.beginner_next_step); must_not_claim=$($blocker.must_not_claim).")
}
$markdownLines.Add("")
$markdownLines.Add("## Evidence")
$markdownLines.Add("")
$markdownLines.Add("- Mobile LAN/WSS preflight: $($packet.evidence.mobile_lan_wss_preflight.status); latest summary result=$($packet.evidence.mobile_lan_wss_preflight.latest_redacted_summary.result)")
$androidTemplate = $packet.evidence.android_real_device_evidence_template.latest_redacted_template
$markdownLines.Add("- Android real-device evidence template: $($packet.evidence.android_real_device_evidence_template.status); found=$($androidTemplate.found); template_status=$($androidTemplate.template_status); real_device_result=$($androidTemplate.real_device_result); pass_claim_allowed=$($androidTemplate.pass_claim_allowed); not_signoff=fail-closed template only, not QR/HTTPS/WSS/certificate/screen/input/revoke/expiry pass evidence.")
$androidGate = $packet.evidence.android_release_gate.latest_redacted_summary
$markdownLines.Add("- Android release gate: $($packet.evidence.android_release_gate.status); latest status=$($androidGate.status); release_ready=$($androidGate.release_ready); preflight_only=$($androidGate.preflight_only); install_claim_allowed=$($androidGate.claim_controls.installable_android_app_claim_allowed); remote_claim_allowed=$($androidGate.claim_controls.real_device_remote_control_claim_allowed); artifact_label=$($androidGate.android_artifact.label); not_signoff=indexed redacted Android gate evidence only, not an APK/install/WSS pass created by this packet.")
$markdownLines.Add("- Mobile remote-input active-grant contract: $($packet.evidence.mobile_remote_input_active_grant_contract.status); scope=$($packet.evidence.mobile_remote_input_active_grant_contract.automated_scope); latest_execution=$($packet.evidence.mobile_remote_input_active_grant_contract.latest_execution_status); verify=$($packet.evidence.mobile_remote_input_active_grant_contract.verify_command); not_signoff=source/client contract only, not live device/WSS.")
$portableCompletionEvidence = $packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.natural_language_completion_evidence
$markdownLines.Add("- Portable first-screen smoke: found=$($packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.found), read_only_pass=$($packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.first_screen_read_only_pass), natural_language=$($packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.natural_language_submission_evidence), completion_evidence.level=$($portableCompletionEvidence.level), result_verified=$($portableCompletionEvidence.result_verified), completed_result_evidence=$($portableCompletionEvidence.completed_result_evidence).")
$markdownLines.Add("- Ollama/local-model contracts: $($packet.evidence.ollama_local_model_contracts.contract_count) backend contract tests counted; latest execution not run by this packet.")
$localModelLatest = $packet.evidence.local_model_clean_machine_template.latest_redacted_clean_machine_template
$markdownLines.Add("- Local model clean-machine template: found=$($localModelLatest.found), template_status=$($localModelLatest.template_status), artifact_status=$($localModelLatest.artifact_build_profile.status), runtime=$($localModelLatest.runtime.name) $($localModelLatest.runtime.version) [$($localModelLatest.runtime.status)], model=$($localModelLatest.model.name) $($localModelLatest.model.version) [$($localModelLatest.model.status)], install=$($localModelLatest.clean_machine_run.install.status), start=$($localModelLatest.clean_machine_run.start.status), pull=$($localModelLatest.clean_machine_run.pull.status), task_smoke=$($localModelLatest.clean_machine_run.task_smoke.status), clean_machine_signoff=$($localModelLatest.clean_machine_signoff), task_smoke_pass=$($localModelLatest.local_model_task_smoke_pass).")
$markdownLines.Add("- Diagnostics external review: expected status=$($packet.evidence.diagnostics_external_review.expected_external_review_status), public_safe=$($packet.evidence.diagnostics_external_review.expected_public_safe).")
$diagnosticsReviewPacket = $packet.evidence.diagnostics_external_review.latest_redacted_review_packet
$markdownLines.Add("- Diagnostics external review packet: found=$($diagnosticsReviewPacket.found), review_status=$($diagnosticsReviewPacket.review_status), public_safe=$($diagnosticsReviewPacket.public_safe), claim_allowed=$($diagnosticsReviewPacket.claim_allowed), review_fields_complete=$($diagnosticsReviewPacket.review_fields_complete), actual_package_content_review_completed=$($diagnosticsReviewPacket.actual_package_content_review_completed), external_sharing_blocked=$($diagnosticsReviewPacket.external_sharing_blocked), separate_human_content_review_required=$($diagnosticsReviewPacket.separate_human_content_review_required).")
$markdownLines.Add("- Result-quality review packet: found=$($packet.evidence.result_quality_review.latest_redacted_review_packet.found), review_status=$($packet.evidence.result_quality_review.latest_redacted_review_packet.review_status), result_quality_signoff=$($packet.evidence.result_quality_review.latest_redacted_review_packet.result_quality_signoff), completed_result_evidence=$($packet.evidence.result_quality_review.latest_redacted_review_packet.completed_result_evidence).")
$rcHandoffTemplate = $packet.evidence.rc_handoff_template.latest_redacted_handoff_template
$markdownLines.Add("- RC handoff template: found=$($rcHandoffTemplate.found), handoff_status=$($rcHandoffTemplate.handoff_status), release_candidate_signoff=$($rcHandoffTemplate.release_candidate_signoff), claim_allowed=$($rcHandoffTemplate.claim_allowed), gate_commands_run_by_this_helper=$($rcHandoffTemplate.gate_commands_run_by_this_helper), missing_required_fields=$($rcHandoffTemplate.missing_required_fields_count).")
$markdownLines.Add("- Settings local-model smoke: $settingsArtifactsPresent/$($settingsArtifactNames.Count) expected screenshot artifacts present.")
$markdownLines.Add("")
$markdownLines.Add("## Next Manual Evidence")
foreach ($item in $packet.next_manual_evidence_needed) {
    $markdownLines.Add("- $item")
}
$markdown = $markdownLines -join "`n"

$packet | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $jsonPath -Encoding utf8
$markdown | Set-Content -LiteralPath $markdownPath -Encoding utf8

Write-Host "Release evidence packet summary"
Write-Host "Redacted JSON: $($packet.outputs.redacted_json)"
Write-Host "Redacted Markdown: $($packet.outputs.redacted_markdown)"
Write-Host ""
Write-Host $markdown

if ($contractFailures.Count -gt 0) {
    Write-Host ""
    Write-Host "[blocked] Release evidence packet source contracts need attention:" -ForegroundColor Red
    foreach ($failure in $contractFailures) {
        Write-Host " - $failure" -ForegroundColor Red
    }
    exit 1
}

exit 0
