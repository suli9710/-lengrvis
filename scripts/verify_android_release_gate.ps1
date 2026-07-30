[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$ArtifactPath = "",
    [string]$RealDeviceEvidencePath = "",
    [string]$OutputRoot = "",
    [string]$ExpectedSignerCertificateSha256 = "",
    [string]$ApkSignerPath = "",
    [string]$AaptPath = "",
    [string]$AndroidBuildToolsRoot = "",
    [string]$ExpectedBuildToolsVersion = "",
    [string]$ExpectedApkSignerSha256 = "",
    [string]$ExpectedApkSignerJarSha256 = "",
    [string]$ExpectedAaptSha256 = "",
    [switch]$PreflightOnly,
    [switch]$RequireCandidateBinding,
    [switch]$TestOnlyAllowUntrustedSdkTools
)

$ErrorActionPreference = "Stop"
$script:ResolvedRootForDisplay = ""

# Windows PowerShell 5.1 can inherit a polluted PSModulePath when the parent
# process is PowerShell 7 (including preview builds). Module autoloading may
# then resolve Microsoft.PowerShell.Utility to an incompatible 7.x copy and
# core cmdlets such as Get-FileHash / ConvertTo-Json disappear. Re-import the
# bundled modules from $PSHOME explicitly to self-heal.
if ($PSVersionTable.PSEdition -eq "Desktop") {
    Import-Module "$PSHOME\Modules\Microsoft.PowerShell.Utility" -ErrorAction SilentlyContinue
    Import-Module "$PSHOME\Modules\Microsoft.PowerShell.Management" -ErrorAction SilentlyContinue
}

function Redact-DisplayLabel {
    param([string]$Label)

    if ([string]::IsNullOrWhiteSpace($Label)) {
        return ""
    }

    $text = $Label.Trim()
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{4,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[redacted-email]")
    $text = [regex]::Replace($text, "(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(Cookie:\s*)[^\r\n]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(Set-Cookie:\s*)[^\r\n]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)([?&](?:token|api[_-]?key|client_secret|secret|password|code|session|cookie|pairing[_-]?code|one[_-]?time[_-]?code|otp)=)[^&\s,;]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)\b(?:token|api[_-]?key|client_secret|secret|password|code|session|cookie|pairing[_-]?code|one[_-]?time[_-]?code|otp)\s*[:=]\s*[^&\s,;]+", "[redacted-sensitive]=[redacted]")
    $text = [regex]::Replace($text, "(?i)\b(pairing code|one-time code|otp)\s+[\w.-]+", '${1} [redacted]')
    $text = [regex]::Replace($text, "(?i)\bhttps?://[^/\s\\]+", "https://[redacted-host]")
    $text = [regex]::Replace($text, "(?i)\bwss?://[^/\s\\]+", "wss://[redacted-host]")
    $text = [regex]::Replace($text, "\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-host]")
    $text = [regex]::Replace($text, "(?i)(?:[A-Z]:\\Users\\[^\\\s]+|/Users/[^/\s]+|/home/[^/\s]+)", "[redacted-home]")
    $text = [regex]::Replace($text, "(?i)\b(?:contoso|acme|customer)[A-Za-z0-9._-]*", "[redacted-org]")
    return $text
}

function Get-DisplayPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }

    try {
        $fullPath = [System.IO.Path]::GetFullPath($Path)
        if (-not [string]::IsNullOrWhiteSpace($script:ResolvedRootForDisplay)) {
            $rootPrefix = $script:ResolvedRootForDisplay.TrimEnd([char[]]@("\", "/")) + [System.IO.Path]::DirectorySeparatorChar
            if ($fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                return (Redact-DisplayLabel ($fullPath.Substring($rootPrefix.Length)))
            }
        }

        return (Redact-DisplayLabel (Split-Path -Leaf $fullPath))
    }
    catch {
        return (Redact-DisplayLabel (Split-Path -Leaf $Path))
    }
}

function Add-Issue {
    param(
        [System.Collections.Generic.List[object]]$Issues,
        [string]$Code,
        [string]$Message,
        [string]$Kind = "blocker"
    )

    $Issues.Add([pscustomobject]@{
        kind = $Kind
        code = $Code
        message = Redact-DisplayLabel $Message
    })
}

function Read-JsonFile {
    param(
        [string]$Path,
        [string]$Label,
        [System.Collections.Generic.List[object]]$Issues
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        Add-Issue $Issues "missing_$Label" "Missing $Label file: $(Get-DisplayPath $Path)"
        return $null
    }

    try {
        return Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    }
    catch {
        Add-Issue $Issues "invalid_$Label" "$Label file is not valid JSON: $(Get-DisplayPath $Path)"
        return $null
    }
}

function Read-TextFile {
    param(
        [string]$Path,
        [string]$Label,
        [System.Collections.Generic.List[object]]$Issues
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        Add-Issue $Issues "missing_$Label" "Missing $Label file: $(Get-DisplayPath $Path)"
        return ""
    }

    try {
        return Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
    }
    catch {
        Add-Issue $Issues "unreadable_$Label" "$Label file could not be read: $(Get-DisplayPath $Path)"
        return ""
    }
}

function Get-Sha256Hex {
    param([string]$Path)

    # Avoid Get-FileHash: it depends on module autoloading, which breaks when a
    # PowerShell 7 parent pollutes PSModulePath for Windows PowerShell 5.1.
    # Plain .NET SHA256 is immune to module resolution issues.
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace "-", "").ToLowerInvariant()
        }
        finally {
            $sha.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Get-PropertyValue {
    param(
        [object]$Value,
        [string]$Name
    )

    if ($null -eq $Value) {
        return $null
    }
    return $Value.PSObject.Properties[$Name].Value
}

function Test-BooleanTrue {
    param([object]$Value)
    return ($Value -is [bool]) -and $Value -eq $true
}

function Test-BooleanFalse {
    param([object]$Value)
    return ($Value -is [bool]) -and $Value -eq $false
}

function Test-StatusPassed {
    param([object]$Value)

    if ($Value -is [string]) {
        return $Value -eq "passed"
    }

    $status = Get-PropertyValue $Value "status"
    return $status -eq "passed"
}

function Test-ApkZipHeader {
    param([string]$Path)

    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            if ($stream.Length -lt 4) {
                return $false
            }

            $buffer = New-Object byte[] 4
            $read = $stream.Read($buffer, 0, 4)
            return ($read -eq 4 -and $buffer[0] -eq 0x50 -and $buffer[1] -eq 0x4B)
        }
        finally {
            $stream.Dispose()
        }
    }
    catch {
        return $false
    }
}

function Test-ApkZipStructure {
    param([string]$Path)

    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
        try {
            $manifestEntry = $archive.GetEntry("AndroidManifest.xml")
            $dexEntry = $archive.Entries | Where-Object { $_.FullName -match "(^|/)classes[0-9]*\.dex$" } | Select-Object -First 1
            if ($null -eq $manifestEntry -or $null -eq $dexEntry) {
                return $false
            }

            $manifestHeader = New-Object byte[] 4
            $manifestStream = $manifestEntry.Open()
            try {
                if ($manifestStream.Read($manifestHeader, 0, 4) -ne 4) {
                    return $false
                }
            }
            finally {
                $manifestStream.Dispose()
            }

            $dexHeader = New-Object byte[] 4
            $dexStream = $dexEntry.Open()
            try {
                if ($dexStream.Read($dexHeader, 0, 4) -ne 4) {
                    return $false
                }
            }
            finally {
                $dexStream.Dispose()
            }

            $manifestLooksBinaryXml = ($manifestHeader[0] -eq 0x03 -and $manifestHeader[1] -eq 0x00 -and $manifestHeader[2] -eq 0x08 -and $manifestHeader[3] -eq 0x00)
            $dexLooksValid = ($dexHeader[0] -eq 0x64 -and $dexHeader[1] -eq 0x65 -and $dexHeader[2] -eq 0x78 -and $dexHeader[3] -eq 0x0A)
            return ($manifestLooksBinaryXml -and $dexLooksValid)
        }
        finally {
            $archive.Dispose()
        }
    }
    catch {
        return $false
    }
}

function Get-SafeArtifactLabel {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }
    return Redact-DisplayLabel ([System.IO.Path]::GetFileName($Path))
}

function Test-ActionableText {
    param([object]$Value)

    if ($null -eq $Value) {
        return $false
    }

    $text = ([string]$Value).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $false
    }
    if ($text -match "^\s*<[^>]+>\s*$") {
        return $false
    }

    return $text -notmatch "(?i)^\s*(todo|tbd|pending|unknown|uncollected|blocked|placeholder|fixme|n/a|na)\s*$"
}

function Test-RedactedFreeText {
    param([object]$Value)

    if (-not (Test-ActionableText $Value)) {
        return $false
    }

    $text = [string]$Value
    if ($text -match "sk-(?:proj-)?[A-Za-z0-9._-]{4,}") {
        return $false
    }
    if ($text -match "(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b") {
        return $false
    }
    if ($text -match "(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*") {
        return $false
    }
    if ($text -match "(?i)(?:token|api[_-]?key|client_secret|secret|password|session|cookie|pairing[_-]?code|one[_-]?time[_-]?code|otp)\s*[:=]\s*[^;\s&]+") {
        return $false
    }
    if ($text -match "(?i)\b(pairing code|one-time code|otp)\s+[\w.-]+") {
        return $false
    }
    if ($text -match "(?i)\bhttps?://(?!\[redacted-host\])[^/\s\\]+") {
        return $false
    }
    if ($text -match "(?i)\bwss?://(?!\[redacted-host\])[^/\s\\]+") {
        return $false
    }
    if ($text -match "\b(?:\d{1,3}\.){3}\d{1,3}\b") {
        return $false
    }
    if ($text -match "(?i)(?:[A-Z]:\\Users\\|/Users/|/home/)") {
        return $false
    }

    return $true
}

function Test-RedactedHttpsOrigin {
    param([object]$Value)

    if (-not (Test-RedactedFreeText $Value)) {
        return $false
    }
    return ([string]$Value) -match "^https://\[redacted-host\](?::\d{1,5})?$"
}

function Test-RedactedWssUrl {
    param([object]$Value)

    if (-not (Test-RedactedFreeText $Value)) {
        return $false
    }
    return ([string]$Value) -match "^wss://\[redacted-host\](?::\d{1,5})?/.+"
}

function Test-UtcTimestamp {
    param([object]$Value)

    if (-not (Test-ActionableText $Value)) {
        return $false
    }

    $text = ([string]$Value).Trim()
    if ($text -notmatch "(Z|[+-]00:00)$") {
        return $false
    }

    try {
        [void][datetimeoffset]::Parse($text, [System.Globalization.CultureInfo]::InvariantCulture)
        return $true
    }
    catch {
        return $false
    }
}

function Get-ReviewStatus {
    param([object]$Evidence)

    $review = Get-PropertyValue $Evidence "review"
    $status = Get-PropertyValue $review "status"
    if ([string]::IsNullOrWhiteSpace([string]$status)) {
        $status = Get-PropertyValue $Evidence "review_status"
    }
    return [string]$status
}

function Get-ReviewerLabel {
    param([object]$Evidence)

    $review = Get-PropertyValue $Evidence "review"
    $label = Get-PropertyValue $review "reviewer_label"
    if ([string]::IsNullOrWhiteSpace([string]$label)) {
        $label = Get-PropertyValue $review "reviewer"
    }
    if ([string]::IsNullOrWhiteSpace([string]$label)) {
        $label = Get-PropertyValue $Evidence "reviewer_label"
    }
    return [string]$label
}

function Get-ReviewedAtUtc {
    param([object]$Evidence)

    $review = Get-PropertyValue $Evidence "review"
    $timestamp = Get-PropertyValue $review "reviewed_at_utc"
    if ([string]::IsNullOrWhiteSpace([string]$timestamp)) {
        $timestamp = Get-PropertyValue $Evidence "reviewed_at_utc"
    }
    return [string]$timestamp
}

function Add-RequiredRedactedValueIssue {
    param(
        [System.Collections.Generic.List[object]]$Issues,
        [string]$Code,
        [string]$Path,
        [object]$Value
    )

    if (-not (Test-RedactedFreeText $Value)) {
        Add-Issue $Issues $Code "Real-device evidence $Path must be a non-placeholder redacted label without raw tokens, hosts, device ids, grant ids, or private paths."
    }
}

function Add-RequiredScriptFragmentIssue {
    param(
        [System.Collections.Generic.List[object]]$Issues,
        [object]$Scripts,
        [string]$ScriptName,
        [string[]]$RequiredFragments
    )

    $command = [string](Get-PropertyValue $Scripts $ScriptName)
    if ([string]::IsNullOrWhiteSpace($command)) {
        return
    }

    foreach ($fragment in $RequiredFragments) {
        if ($command.IndexOf($fragment, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            Add-Issue $Issues "script_contract_mismatch" "package script '$ScriptName' must include '$fragment' so Android build/release evidence is fail-closed."
        }
    }
}

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$script:ResolvedRootForDisplay = $resolvedRoot
$mobileRoot = Join-Path $resolvedRoot "mobile"
$sourceIssues = New-Object System.Collections.Generic.List[object]
$artifactIssues = New-Object System.Collections.Generic.List[object]
$deviceIssues = New-Object System.Collections.Generic.List[object]
$warnings = New-Object System.Collections.Generic.List[object]
$strictEvidenceContract = [ordered]@{
    artifact_type = "android-real-device-remote-control-evidence"
    review_status = "reviewed_passed"
    reviewer_fields = @("review.reviewer_label", "review.reviewed_at_utc")
    app_fields = @(
        "app.artifact_sha256",
        "app.artifact_label_redacted",
        "app.build_profile",
        "app.eas_build_label_redacted",
        "app.package_name",
        "app.version_name",
        "app.version_code",
        "app.signer_certificate_sha256",
        "app.provenance"
    )
    device_fields = @("device.kind", "device.profile_label_redacted")
    transport_fields = @(
        "transport.https_origin_redacted",
        "transport.approval_wss_origin_redacted",
        "transport.remote_screen_wss_origin_redacted",
        "transport.remote_input_wss_origin_redacted"
    )
    evidence_labels = "At least one reviewed redacted screenshot/video/log label in evidence_artifacts_redacted."
    sensitive_values = "No raw tokens, pairing codes, hosts/IPs, device ids, grant ids, or private paths in shareable labels."
    reviewed_evidence_signature = "A sealed HMAC evidence block is required for all full Android release gates."
    artifact_manifest = "The sealed evidence must bind redacted screenshot/video, backend/mobile logs, and adb install-status artifacts by SHA-256 and byte size."
    signing_key_fingerprint = "The reviewed-evidence signature payload must cryptographically bind its signing-key fingerprint label."
    candidate_binding = "Strict RC runs require the sealed evidence candidate identity to match the explicit checked-out candidate."
    apk_signature = "Android SDK apksigner verify --verbose --print-certs must pass with v2 and v3 schemes and one controlled signer certificate."
    sdk_toolchain = "apksigner.bat, apksigner.jar, and aapt2.exe must come from one approved build-tools/<version> root and match protected SHA-256 values. PATH and individual tool overrides are not trusted."
    merged_manifest = "The final binary AndroidManifest.xml must disable debuggable, testOnly, backup, and cleartext traffic and must not expose unprotected non-launcher components."
    artifact_provenance = "The signed reviewed evidence must bind candidate source, builder invocation, APK digest, package/version, and signer certificate digest."
}

$appJsonPath = Join-Path $mobileRoot "app.json"
$easJsonPath = Join-Path $mobileRoot "eas.json"
$mobilePackagePath = Join-Path $mobileRoot "package.json"
$androidGradlePath = Join-Path $mobileRoot "android\app\build.gradle"
$rootPackagePath = Join-Path $resolvedRoot "package.json"
$androidNetworkConfigPaths = @(
    (Join-Path $mobileRoot "android\app\src\main\res\xml\network_security_config.xml"),
    (Join-Path $mobileRoot "android\app\src\debug\res\xml\network_security_config.xml"),
    (Join-Path $mobileRoot "android\app\src\debugOptimized\res\xml\network_security_config.xml")
)
$androidNetworkConfigSources = @{}
foreach ($networkConfigPath in $androidNetworkConfigPaths) {
    $androidNetworkConfigSources[$networkConfigPath] = Read-TextFile $networkConfigPath "android_network_security_config" $sourceIssues
}
$androidMainApplicationSource = Read-TextFile (Join-Path $mobileRoot "android\app\src\main\java\com\lengrvis\approval\MainApplication.kt") "android_main_application" $sourceIssues
$androidLanTrustSource = Read-TextFile (Join-Path $mobileRoot "android\app\src\main\java\com\lengrvis\approval\LengrvisLanTrust.kt") "android_lan_trust" $sourceIssues
$androidLanTrustInstrumentationSource = Read-TextFile (Join-Path $mobileRoot "android\app\src\androidTest\java\com\lengrvis\approval\LengrvisLanTrustInstrumentedTest.kt") "android_lan_trust_instrumentation" $sourceIssues

$appJson = Read-JsonFile $appJsonPath "mobile_app_json" $sourceIssues
$easJson = Read-JsonFile $easJsonPath "mobile_eas_json" $sourceIssues
$mobilePackage = Read-JsonFile $mobilePackagePath "mobile_package_json" $sourceIssues
$androidGradleSource = Read-TextFile $androidGradlePath "android_app_build_gradle" $sourceIssues
$rootPackage = Read-JsonFile $rootPackagePath "root_package_json" $sourceIssues
$expectedAndroidPackageName = ""
$expectedAndroidVersionName = ""
$expectedAndroidVersionCode = 0

foreach ($fragment in @(
    "releaseSigningConfigured",
    "releaseTaskRequested",
    "throw new GradleException",
    "signingConfig signingConfigs.release",
    "enableV2Signing true",
    "enableV3Signing true"
)) {
    if ($androidGradleSource.IndexOf($fragment, [System.StringComparison]::Ordinal) -lt 0) {
        Add-Issue $sourceIssues "android_release_signing_not_fail_closed" "mobile/android/app/build.gradle must include '$fragment' in its fail-closed release signing path."
    }
}
if ($androidGradleSource.IndexOf("release.keystore", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
    Add-Issue $sourceIssues "android_placeholder_release_keystore" "Android release signing must not fall back to a placeholder release.keystore."
}

if ($null -ne $appJson) {
    $expo = Get-PropertyValue $appJson "expo"
    $android = Get-PropertyValue $expo "android"
    $plugins = Get-PropertyValue $expo "plugins"
    $easProjectId = Get-PropertyValue (Get-PropertyValue (Get-PropertyValue $expo "extra") "eas") "projectId"
    $expectedAndroidPackageName = [string](Get-PropertyValue $android "package")
    $expectedAndroidVersionName = [string](Get-PropertyValue $expo "version")
    $configuredVersionCode = Get-PropertyValue $android "versionCode"
    if ($configuredVersionCode -is [int] -or $configuredVersionCode -is [long]) {
        $expectedAndroidVersionCode = [int]$configuredVersionCode
    }

    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $expo "name"))) {
        Add-Issue $sourceIssues "missing_app_name" "mobile/app.json must define expo.name for an installable Android artifact."
    }
    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $expo "slug"))) {
        Add-Issue $sourceIssues "missing_app_slug" "mobile/app.json must define expo.slug."
    }
    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $expo "version"))) {
        Add-Issue $sourceIssues "missing_app_version" "mobile/app.json must define expo.version."
    }
    else {
        $nativeVersionNameMatch = [regex]::Match($androidGradleSource, '(?m)^\s*versionName\s+["''](?<version>[^"'']+)["'']\s*$')
        if (-not $nativeVersionNameMatch.Success) {
            Add-Issue $sourceIssues "missing_android_native_version_name" "mobile/android/app/build.gradle must declare a literal Android versionName that matches expo.version."
        }
        elseif ($nativeVersionNameMatch.Groups["version"].Value -ne (Get-PropertyValue $expo "version")) {
            Add-Issue $sourceIssues "android_native_version_name_mismatch" "mobile/android/app/build.gradle versionName must match expo.version."
        }
    }
    if ((Get-PropertyValue $expo "orientation") -ne "default") {
        Add-Issue $sourceIssues "android_landscape_not_enabled" "Remote desktop viewing requires expo.orientation=default so Android can use landscape."
    }
    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $android "package"))) {
        Add-Issue $sourceIssues "missing_android_package" "mobile/app.json must define expo.android.package."
    }
    if ([string]::IsNullOrWhiteSpace($easProjectId)) {
        Add-Issue $warnings "eas_project_id_not_recorded" "No expo.extra.eas.projectId is recorded. The APK build log must prove the EAS project, account, and credentials used for the artifact." "warning"
    }

    $versionCode = Get-PropertyValue $android "versionCode"
    if (-not ($versionCode -is [int]) -and -not ($versionCode -is [long])) {
        Add-Issue $sourceIssues "missing_android_version_code" "mobile/app.json must define integer expo.android.versionCode for release builds."
    }
    elseif ($versionCode -lt 1) {
        Add-Issue $sourceIssues "invalid_android_version_code" "expo.android.versionCode must be greater than zero."
    }
    else {
        $nativeVersionCodeMatch = [regex]::Match($androidGradleSource, '(?m)^\s*versionCode\s+(?<version>\d+)\s*$')
        if (-not $nativeVersionCodeMatch.Success) {
            Add-Issue $sourceIssues "missing_android_native_version_code" "mobile/android/app/build.gradle must declare a literal Android versionCode that matches expo.android.versionCode."
        }
        elseif ([int]$nativeVersionCodeMatch.Groups["version"].Value -ne [int]$versionCode) {
            Add-Issue $sourceIssues "android_native_version_code_mismatch" "mobile/android/app/build.gradle versionCode must match expo.android.versionCode."
        }
    }

    if (-not (Test-BooleanFalse (Get-PropertyValue $android "usesCleartextTraffic"))) {
        Add-Issue $sourceIssues "cleartext_traffic_not_disabled" "expo.android.usesCleartextTraffic must stay false for token-bearing mobile flows."
    }
    if ((Get-PropertyValue $android "softwareKeyboardLayoutMode") -ne "resize") {
        Add-Issue $sourceIssues "keyboard_resize_missing" "expo.android.softwareKeyboardLayoutMode must be resize for Android remote-control text inputs."
    }

    $permissions = @(Get-PropertyValue $android "permissions")
    foreach ($permission in @("CAMERA", "POST_NOTIFICATIONS")) {
        if ($permissions -notcontains $permission) {
            Add-Issue $sourceIssues "missing_android_permission" "expo.android.permissions must include $permission."
        }
    }
    $blockedPermissions = @(Get-PropertyValue $android "blockedPermissions")
    foreach ($permission in @(
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.SYSTEM_ALERT_WINDOW",
        "android.permission.WRITE_EXTERNAL_STORAGE"
    )) {
        if ($blockedPermissions -notcontains $permission) {
            Add-Issue $sourceIssues "android_permission_not_blocked" "expo.android.blockedPermissions must include $permission."
        }
    }

    $cameraPlugin = $null
    $hardeningPluginFound = $false
    foreach ($plugin in @($plugins)) {
        if ($plugin -is [array] -and $plugin.Count -gt 0 -and $plugin[0] -eq "expo-camera") {
            $cameraPlugin = $plugin
        }
        elseif ([string]$plugin -eq "./plugins/withAndroidRemoteControlHardening") {
            $hardeningPluginFound = $true
        }
    }
    if ($null -eq $cameraPlugin) {
        Add-Issue $sourceIssues "missing_expo_camera_plugin" "mobile/app.json must configure the expo-camera plugin for QR pairing."
    }
    elseif ($cameraPlugin.Count -gt 1) {
        $cameraOptions = $cameraPlugin[1]
        if (-not (Test-BooleanFalse (Get-PropertyValue $cameraOptions "recordAudioAndroid"))) {
            Add-Issue $sourceIssues "camera_audio_not_disabled" "expo-camera recordAudioAndroid must stay false; QR pairing does not need microphone capture."
        }
    }

    $hardeningPluginPath = Join-Path $mobileRoot "plugins\withAndroidRemoteControlHardening.js"
    $hardeningPluginSource = Read-TextFile $hardeningPluginPath "android_remote_control_hardening_plugin" $sourceIssues
    if (-not $hardeningPluginFound) {
        Add-Issue $sourceIssues "android_hardening_plugin_missing" "mobile/app.json must include ./plugins/withAndroidRemoteControlHardening so EAS builds inject network security and FLAG_SECURE."
    }
    foreach ($fragment in @(
        "network_security_config",
        'certificates src="system"',
        'cleartextTrafficPermitted="false"',
        'mainApplication.$["android:allowBackup"] = "false"',
        "AndroidConfig.Permissions.removePermissions",
        "android:networkSecurityConfig",
        "android:usesCleartextTraffic",
        "WindowManager.LayoutParams.FLAG_SECURE"
    )) {
        if ($hardeningPluginSource.IndexOf($fragment, [System.StringComparison]::Ordinal) -lt 0) {
            Add-Issue $sourceIssues "android_hardening_plugin_contract_mismatch" "Android hardening plugin must include '$fragment' for LAN TLS trust and remote-screen screenshot protection."
        }
    }
    if ($hardeningPluginSource -match '<certificates\s+src="user"') {
        Add-Issue $sourceIssues "android_user_ca_trust_enabled" "Android release network security must not trust user-installed CAs by default."
    }
    foreach ($networkConfigPath in $androidNetworkConfigPaths) {
        $networkConfigSource = [string]$androidNetworkConfigSources[$networkConfigPath]
        if ($networkConfigSource.IndexOf('certificates src="system"', [System.StringComparison]::Ordinal) -lt 0) {
            Add-Issue $sourceIssues "android_system_ca_trust_missing" "Android network security config $(Get-DisplayPath $networkConfigPath) must retain the system trust anchor."
        }
        if ($networkConfigSource -match '<certificates\s+src="user"') {
            Add-Issue $sourceIssues "android_user_ca_trust_enabled" "Android network security config $(Get-DisplayPath $networkConfigPath) must not trust user-installed CAs."
        }
    }
    foreach ($fragment in @("LengrvisLanTrust.install(this)")) {
        if ($androidMainApplicationSource.IndexOf($fragment, [System.StringComparison]::Ordinal) -lt 0) {
            Add-Issue $sourceIssues "android_lan_tls_install_missing" "Android application startup must include '$fragment' so React Native HTTPS and WSS use the pinned client."
        }
    }
    foreach ($fragment in @("OkHttpClientProvider.setOkHttpClientFactory", ".sslSocketFactory", "AndroidCAStore", 'alias.startsWith("system:")', "hasAnyFingerprint", "hostHasFingerprint", "hostHasAnyFingerprintForHost")) {
        if ($androidLanTrustSource.IndexOf($fragment, [System.StringComparison]::Ordinal) -lt 0) {
            Add-Issue $sourceIssues "android_lan_tls_pin_contract_mismatch" "Android LAN TLS trust implementation must include '$fragment' for per-host certificate pinning."
        }
    }
    foreach ($fragment in @("assertTlsHandshakeFails", "wrongFingerprint(fingerprintSha256)", "LengrvisLanTrust.trustServerCertificate(context, baseUrl, fingerprintSha256)", "OkHttpClientProvider.createClient(context)", "/api/health", "/api/pair/confirm", "/ws/mobile/approvals", 'connected.contains("\"type\":\"connected\"")')) {
        if ($androidLanTrustInstrumentationSource.IndexOf($fragment, [System.StringComparison]::Ordinal) -lt 0) {
            Add-Issue $sourceIssues "android_lan_tls_instrumentation_contract_mismatch" "Android LAN TLS instrumentation must include '$fragment' so release evidence covers wrong-pin failure, pinned HTTPS pairing, and approval WSS."
        }
    }
}

function ConvertTo-Sha256Hex {
    param([object]$Value)

    if ($null -eq $Value) {
        return ""
    }
    $text = ([string]$Value).Trim()
    if ($text -notmatch "^[0-9A-Fa-f:\s-]+$") {
        return ""
    }
    $normalized = $text -replace "[:\s-]", ""
    if ($normalized.Length -ne 64) {
        return ""
    }
    return $normalized.ToLowerInvariant()
}

function Get-ConfiguredValue {
    param(
        [string]$ExplicitValue,
        [string]$EnvironmentName
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitValue)) {
        return $ExplicitValue.Trim()
    }
    return ([string][Environment]::GetEnvironmentVariable($EnvironmentName)).Trim()
}

function Test-FileHasPrefix {
    param(
        [string]$Path,
        [byte[]]$ExpectedPrefix
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($stream.Length -lt $ExpectedPrefix.Length) {
            return $false
        }
        foreach ($expected in $ExpectedPrefix) {
            if ($stream.ReadByte() -ne $expected) {
                return $false
            }
        }
        return $true
    }
    finally {
        $stream.Dispose()
    }
}

function Test-ZipContainsEntry {
    param(
        [string]$Path,
        [string]$EntryName
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
        try {
            return $null -ne $archive.GetEntry($EntryName)
        }
        finally {
            $archive.Dispose()
        }
    }
    catch {
        return $false
    }
}

function Test-PathHasReparsePoint {
    param([string]$Path)

    try {
        $item = Get-Item -LiteralPath $Path -Force
        return (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    }
    catch {
        return $true
    }
}

function Resolve-TrustedAndroidBuildTools {
    param(
        [System.Collections.Generic.List[object]]$Issues
    )

    $startIssueCount = $Issues.Count
    $result = [ordered]@{
        evaluated = $true
        test_only = [bool]$TestOnlyAllowUntrustedSdkTools
        trusted_root_label = ""
        expected_version = ""
        source_properties_version = ""
        source_properties_path = ""
        apksigner_path = ""
        apksigner_sha256 = ""
        apksigner_jar_path = ""
        apksigner_jar_sha256 = ""
        aapt_path = ""
        aapt_sha256 = ""
        provenance_verified = $false
    }

    if ($TestOnlyAllowUntrustedSdkTools) {
        Add-Issue $Issues "test_only_android_sdk_tools" "Test-only Android SDK tool shims can exercise output parsing but can never satisfy strict release readiness."
        if (Test-Path -LiteralPath $ApkSignerPath -PathType Leaf) {
            $result.apksigner_path = (Resolve-Path -LiteralPath $ApkSignerPath).Path
        }
        if (Test-Path -LiteralPath $AaptPath -PathType Leaf) {
            $result.aapt_path = (Resolve-Path -LiteralPath $AaptPath).Path
        }
        return [pscustomobject]$result
    }

    if (-not [string]::IsNullOrWhiteSpace($ApkSignerPath) -or -not [string]::IsNullOrWhiteSpace($AaptPath)) {
        Add-Issue $Issues "android_explicit_sdk_tool_path_forbidden" "Strict Android release validation does not accept individual apksigner/aapt paths; configure one approved build-tools root instead."
    }

    $expectedVersion = Get-ConfiguredValue $ExpectedBuildToolsVersion "LENGRVIS_ANDROID_BUILD_TOOLS_VERSION"
    $trustedRootInput = Get-ConfiguredValue $AndroidBuildToolsRoot "LENGRVIS_ANDROID_BUILD_TOOLS_ROOT"
    if ([string]::IsNullOrWhiteSpace($trustedRootInput) -and -not [string]::IsNullOrWhiteSpace($expectedVersion)) {
        $sdkRoot = [string][Environment]::GetEnvironmentVariable("ANDROID_SDK_ROOT")
        if ([string]::IsNullOrWhiteSpace($sdkRoot)) {
            $sdkRoot = [string][Environment]::GetEnvironmentVariable("ANDROID_HOME")
        }
        if (-not [string]::IsNullOrWhiteSpace($sdkRoot)) {
            $trustedRootInput = Join-Path (Join-Path $sdkRoot "build-tools") $expectedVersion
        }
    }
    $result.expected_version = $expectedVersion

    if ([string]::IsNullOrWhiteSpace($expectedVersion)) {
        Add-Issue $Issues "android_build_tools_version_missing" "Strict Android release validation requires -ExpectedBuildToolsVersion or LENGRVIS_ANDROID_BUILD_TOOLS_VERSION."
    }
    elseif ($expectedVersion -notmatch '^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$') {
        Add-Issue $Issues "android_build_tools_version_invalid" "The expected Android build-tools version must be a concrete semantic version."
    }
    if ([string]::IsNullOrWhiteSpace($trustedRootInput)) {
        Add-Issue $Issues "android_build_tools_root_missing" "Strict Android release validation requires an approved build-tools root, not PATH discovery."
        return [pscustomobject]$result
    }
    if (-not (Test-Path -LiteralPath $trustedRootInput -PathType Container)) {
        Add-Issue $Issues "android_build_tools_root_not_found" "The configured Android build-tools root does not exist: $(Get-DisplayPath $trustedRootInput)"
        return [pscustomobject]$result
    }

    $trustedRoot = (Resolve-Path -LiteralPath $trustedRootInput).Path
    $result.trusted_root_label = Get-DisplayPath $trustedRoot
    if (Test-PathHasReparsePoint $trustedRoot) {
        Add-Issue $Issues "android_build_tools_root_reparse_point" "The approved Android build-tools root must not be a symlink or reparse point."
    }
    if ((Split-Path -Leaf (Split-Path -Parent $trustedRoot)) -ne "build-tools") {
        Add-Issue $Issues "android_build_tools_root_shape_invalid" "The approved tool root must be an exact Android SDK build-tools/<version> directory."
    }
    if (-not [string]::IsNullOrWhiteSpace($expectedVersion) -and (Split-Path -Leaf $trustedRoot) -ne $expectedVersion) {
        Add-Issue $Issues "android_build_tools_root_version_mismatch" "The approved build-tools directory name must equal the expected build-tools version."
    }

    $sourcePropertiesPath = Join-Path $trustedRoot "source.properties"
    $result.source_properties_path = Get-DisplayPath $sourcePropertiesPath
    if (-not (Test-Path -LiteralPath $sourcePropertiesPath -PathType Leaf)) {
        Add-Issue $Issues "android_build_tools_source_properties_missing" "The approved build-tools root must contain Android SDK source.properties provenance."
    }
    else {
        $sourceProperties = Get-Content -Raw -Encoding UTF8 -LiteralPath $sourcePropertiesPath
        $revisionMatch = [regex]::Match($sourceProperties, '(?im)^\s*Pkg\.Revision\s*=\s*(?<version>[^\r\n]+)\s*$')
        if ($revisionMatch.Success) {
            $result.source_properties_version = $revisionMatch.Groups["version"].Value.Trim()
        }
        if (-not $revisionMatch.Success -or $result.source_properties_version -ne $expectedVersion) {
            Add-Issue $Issues "android_build_tools_revision_mismatch" "source.properties Pkg.Revision must equal the expected build-tools version."
        }
    }

    $apksignerPath = Join-Path $trustedRoot "apksigner.bat"
    $apksignerJarPath = Join-Path (Join-Path $trustedRoot "lib") "apksigner.jar"
    $aaptPath = Join-Path $trustedRoot "aapt2.exe"
    foreach ($tool in @(
        [pscustomobject]@{ path = $apksignerPath; code = "android_apksigner_not_found"; label = "apksigner.bat" },
        [pscustomobject]@{ path = $apksignerJarPath; code = "android_apksigner_jar_not_found"; label = "lib/apksigner.jar" },
        [pscustomobject]@{ path = $aaptPath; code = "android_aapt_not_found"; label = "aapt2.exe" }
    )) {
        if (-not (Test-Path -LiteralPath $tool.path -PathType Leaf)) {
            Add-Issue $Issues $tool.code "The approved build-tools root is missing $($tool.label)."
        }
        elseif (Test-PathHasReparsePoint $tool.path) {
            Add-Issue $Issues "android_sdk_tool_reparse_point" "Approved Android SDK tools must not be symlinks or reparse points."
        }
    }
    if (Test-Path -LiteralPath $apksignerPath -PathType Leaf) {
        $result.apksigner_path = (Resolve-Path -LiteralPath $apksignerPath).Path
        $result.apksigner_sha256 = Get-Sha256Hex $result.apksigner_path
        $launcher = Get-Content -Raw -Encoding UTF8 -LiteralPath $result.apksigner_path
        if ($launcher -notmatch '(?i)apksigner\.jar' -or $launcher -notmatch '(?i)\bjava(?:\.exe)?\b') {
            Add-Issue $Issues "android_apksigner_launcher_invalid" "apksigner.bat does not match the canonical Android SDK Java launcher shape."
        }
    }
    if (Test-Path -LiteralPath $apksignerJarPath -PathType Leaf) {
        $result.apksigner_jar_path = (Resolve-Path -LiteralPath $apksignerJarPath).Path
        $result.apksigner_jar_sha256 = Get-Sha256Hex $result.apksigner_jar_path
        if (-not (Test-ZipContainsEntry $result.apksigner_jar_path "com/android/apksigner/ApkSignerTool.class")) {
            Add-Issue $Issues "android_apksigner_jar_invalid" "apksigner.jar does not contain the expected Android SDK signer entrypoint."
        }
    }
    if (Test-Path -LiteralPath $aaptPath -PathType Leaf) {
        $result.aapt_path = (Resolve-Path -LiteralPath $aaptPath).Path
        $result.aapt_sha256 = Get-Sha256Hex $result.aapt_path
        if (-not (Test-FileHasPrefix $result.aapt_path ([byte[]]@(0x4d, 0x5a)))) {
            Add-Issue $Issues "android_aapt_binary_invalid" "aapt2.exe must be a Windows PE binary from the approved Android SDK package."
        }
    }

    foreach ($digestSpec in @(
        [pscustomobject]@{ supplied = (Get-ConfiguredValue $ExpectedApkSignerSha256 "LENGRVIS_ANDROID_APKSIGNER_SHA256"); actual = $result.apksigner_sha256; missing = "android_apksigner_expected_sha256_missing"; mismatch = "android_apksigner_sha256_mismatch"; label = "apksigner.bat" },
        [pscustomobject]@{ supplied = (Get-ConfiguredValue $ExpectedApkSignerJarSha256 "LENGRVIS_ANDROID_APKSIGNER_JAR_SHA256"); actual = $result.apksigner_jar_sha256; missing = "android_apksigner_jar_expected_sha256_missing"; mismatch = "android_apksigner_jar_sha256_mismatch"; label = "apksigner.jar" },
        [pscustomobject]@{ supplied = (Get-ConfiguredValue $ExpectedAaptSha256 "LENGRVIS_ANDROID_AAPT_SHA256"); actual = $result.aapt_sha256; missing = "android_aapt_expected_sha256_missing"; mismatch = "android_aapt_sha256_mismatch"; label = "aapt2.exe" }
    )) {
        $expectedDigest = ConvertTo-Sha256Hex $digestSpec.supplied
        if ([string]::IsNullOrWhiteSpace($digestSpec.supplied)) {
            Add-Issue $Issues $digestSpec.missing "Strict Android release validation requires a protected expected SHA-256 for $($digestSpec.label)."
        }
        elseif ([string]::IsNullOrWhiteSpace($expectedDigest)) {
            Add-Issue $Issues "android_sdk_tool_expected_sha256_invalid" "Expected Android SDK tool SHA-256 values must contain exactly 64 hexadecimal characters."
        }
        elseif ([string]::IsNullOrWhiteSpace($digestSpec.actual) -or $digestSpec.actual -ne $expectedDigest) {
            Add-Issue $Issues $digestSpec.mismatch "The approved $($digestSpec.label) digest does not match protected release configuration."
        }
    }

    $result.provenance_verified = $Issues.Count -eq $startIssueCount
    if (-not $result.provenance_verified) {
        $result.apksigner_path = ""
        $result.aapt_path = ""
    }
    return [pscustomobject]$result
}

function Invoke-ApkSignerVerification {
    param(
        [string]$ToolPath,
        [string]$ApkPath
    )

    $result = [ordered]@{
        evaluated = $false
        tool_label = Get-SafeArtifactLabel $ToolPath
        command = "apksigner verify --verbose --print-certs"
        verification_succeeded = $false
        v2_verified = $false
        v3_verified = $false
        signer_count = 0
        signer_certificate_sha256 = ""
    }
    if ([string]::IsNullOrWhiteSpace($ToolPath)) {
        return [pscustomobject]$result
    }

    $result.evaluated = $true
    try {
        $global:LASTEXITCODE = 0
        $output = & $ToolPath verify --verbose --print-certs $ApkPath 2>&1 | ForEach-Object { [string]$_ }
        $exitCode = $LASTEXITCODE
        $outputText = $output -join "`n"
        $result.verification_succeeded = $exitCode -eq 0
        $result.v2_verified = [regex]::IsMatch($outputText, '(?im)^\s*Verified using v2 scheme(?:\s*\([^\r\n]*\))?:\s*true\s*$')
        $result.v3_verified = [regex]::IsMatch($outputText, '(?im)^\s*Verified using v3 scheme(?:\s*\([^\r\n]*\))?:\s*true\s*$')

        $signerCountMatch = [regex]::Match($outputText, '(?im)^\s*Number of signers:\s*(?<count>\d+)\s*$')
        if ($signerCountMatch.Success) {
            $result.signer_count = [int]$signerCountMatch.Groups["count"].Value
        }
        $certificateMatches = [regex]::Matches($outputText, '(?im)^\s*Signer #\d+ certificate SHA-256 digest:\s*(?<digest>[0-9A-Fa-f: ]+)\s*$')
        $certificateDigests = @($certificateMatches | ForEach-Object { ConvertTo-Sha256Hex $_.Groups["digest"].Value } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
        if ($certificateDigests.Count -eq 1) {
            $result.signer_certificate_sha256 = $certificateDigests[0]
        }
    }
    catch {
        $result.verification_succeeded = $false
    }
    return [pscustomobject]$result
}

function Get-AaptAttributeText {
    param([string]$Line)

    $rawMatch = [regex]::Match($Line, '\(Raw:\s*"(?<value>[^"]*)"\)')
    if ($rawMatch.Success) {
        return $rawMatch.Groups["value"].Value
    }
    $quotedMatch = [regex]::Match($Line, '=\s*"(?<value>[^"]*)"')
    if ($quotedMatch.Success) {
        return $quotedMatch.Groups["value"].Value
    }
    return ""
}

function Get-AaptAttributeName {
    param([string]$RawName)

    $name = [string]$RawName
    $androidNamespaceMarker = "/apk/res/android:"
    $markerIndex = $name.LastIndexOf($androidNamespaceMarker, [System.StringComparison]::OrdinalIgnoreCase)
    if ($markerIndex -ge 0) {
        return "android:" + $name.Substring($markerIndex + $androidNamespaceMarker.Length)
    }
    return $name
}

function Get-AaptBooleanText {
    param([string]$Line)

    if ($Line -match '(?i)(?:Raw:\s*")?true"?' -or $Line -match '(?i)\(type\s+0x12\)0xffffffff\b') {
        return "true"
    }
    if ($Line -match '(?i)(?:Raw:\s*")?false"?' -or $Line -match '(?i)\(type\s+0x12\)0x0+\b') {
        return "false"
    }
    return "unknown"
}

function Inspect-AaptXmlTreeHardening {
    param([string[]]$Lines)

    $result = [ordered]@{
        inspection_succeeded = $false
        debuggable_declared = $false
        debuggable = $false
        test_only_declared = $false
        test_only = $false
        allow_backup_declared = $false
        allow_backup = $true
        cleartext_traffic_declared = $false
        uses_cleartext_traffic = $true
        unsafe_exported_components = @()
        component_count = 0
    }
    $componentTags = @("activity", "activity-alias", "service", "receiver", "provider")
    $components = New-Object System.Collections.Generic.List[object]
    $sawManifest = $false
    $insideApplication = $false
    $applicationIndent = -1
    $currentComponent = $null
    $currentElementTag = ""

    foreach ($rawLine in $Lines) {
        $line = [string]$rawLine
        $indent = [regex]::Match($line, '^\s*').Value.Length
        $elementMatch = [regex]::Match($line, '^\s*E:\s+(?<tag>[^\s(]+)')
        if ($elementMatch.Success) {
            $tag = $elementMatch.Groups["tag"].Value
            if ($tag -eq "manifest") {
                $sawManifest = $true
            }
            if ($null -ne $currentComponent -and $indent -le [int]$currentComponent.indent) {
                $currentComponent = $null
            }
            if ($insideApplication -and $indent -le $applicationIndent -and $tag -ne "application") {
                $insideApplication = $false
            }
            if ($tag -eq "application") {
                $insideApplication = $true
                $applicationIndent = $indent
            }
            elseif ($insideApplication -and $componentTags -contains $tag) {
                $currentComponent = [pscustomobject][ordered]@{
                    kind = $tag
                    indent = $indent
                    name = ""
                    exported = "unknown"
                    permission = ""
                    has_intent_filter = $false
                    actions = New-Object System.Collections.Generic.List[string]
                    categories = New-Object System.Collections.Generic.List[string]
                }
                $components.Add($currentComponent)
            }
            elseif ($insideApplication -and $null -ne $currentComponent -and $tag -eq "intent-filter") {
                $currentComponent.has_intent_filter = $true
            }
            $currentElementTag = $tag
            continue
        }

        $attributeMatch = [regex]::Match($line, '^\s*A:\s+(?<name>[^=\s(]+)(?:\([^)]*\))?=')
        if (-not $attributeMatch.Success -or -not $insideApplication) {
            continue
        }
        $attributeName = Get-AaptAttributeName $attributeMatch.Groups["name"].Value
        if ($null -eq $currentComponent -and $currentElementTag -eq "application") {
            $booleanText = Get-AaptBooleanText $line
            switch ($attributeName) {
                "android:debuggable" {
                    $result.debuggable_declared = $true
                    $result.debuggable = $booleanText -ne "false"
                }
                "android:testOnly" {
                    $result.test_only_declared = $true
                    $result.test_only = $booleanText -ne "false"
                }
                "android:allowBackup" {
                    $result.allow_backup_declared = $true
                    $result.allow_backup = $booleanText -ne "false"
                }
                "android:usesCleartextTraffic" {
                    $result.cleartext_traffic_declared = $true
                    $result.uses_cleartext_traffic = $booleanText -ne "false"
                }
            }
            continue
        }
        if ($null -eq $currentComponent -or $indent -le [int]$currentComponent.indent) {
            continue
        }
        $attributeText = Get-AaptAttributeText $line
        if ($currentElementTag -eq [string]$currentComponent.kind) {
            switch ($attributeName) {
                "android:name" { $currentComponent.name = $attributeText }
                "android:exported" { $currentComponent.exported = Get-AaptBooleanText $line }
                "android:permission" { $currentComponent.permission = $attributeText }
            }
        }
        elseif ($currentElementTag -eq "action" -and $attributeName -eq "android:name" -and -not [string]::IsNullOrWhiteSpace($attributeText)) {
            $currentComponent.actions.Add($attributeText)
        }
        elseif ($currentElementTag -eq "category" -and $attributeName -eq "android:name" -and -not [string]::IsNullOrWhiteSpace($attributeText)) {
            $currentComponent.categories.Add($attributeText)
        }
    }

    $unsafe = New-Object System.Collections.Generic.List[string]
    foreach ($component in $components) {
        $launcher = (
            @($component.actions) -contains "android.intent.action.MAIN" -and
            @($component.categories) -contains "android.intent.category.LAUNCHER"
        )
        $permissionProtected = -not [string]::IsNullOrWhiteSpace([string]$component.permission)
        $unsafeExport = (
            $component.exported -eq "true" -and
            -not $launcher -and
            -not $permissionProtected
        )
        $missingExported = $component.has_intent_filter -and $component.exported -eq "unknown"
        if ($unsafeExport -or $missingExported) {
            $label = if ([string]::IsNullOrWhiteSpace([string]$component.name)) { "<unnamed>" } else { [string]$component.name }
            $unsafe.Add("$($component.kind):$label")
        }
    }
    $result.component_count = $components.Count
    $result.unsafe_exported_components = @($unsafe.ToArray())
    $result.inspection_succeeded = $sawManifest -and $applicationIndent -ge 0
    return [pscustomobject]$result
}

function Invoke-ApkManifestInspection {
    param(
        [string]$ToolPath,
        [string]$ApkPath
    )

    $result = [ordered]@{
        evaluated = $false
        tool_label = Get-SafeArtifactLabel $ToolPath
        command = "aapt dump badging + dump xmltree AndroidManifest.xml"
        inspection_succeeded = $false
        xmltree_inspection_succeeded = $false
        package_name = ""
        version_name = ""
        version_code = 0
        debuggable_declared = $false
        debuggable = $false
        test_only_declared = $false
        test_only = $false
        allow_backup_declared = $false
        allow_backup = $true
        cleartext_traffic_declared = $false
        uses_cleartext_traffic = $true
        unsafe_exported_components = @()
        component_count = 0
        hardening_verified = $false
    }
    if ([string]::IsNullOrWhiteSpace($ToolPath)) {
        return [pscustomobject]$result
    }

    $result.evaluated = $true
    try {
        $global:LASTEXITCODE = 0
        $badgingOutput = & $ToolPath dump badging $ApkPath 2>&1 | ForEach-Object { [string]$_ }
        $badgingExitCode = $LASTEXITCODE
        $badgingText = $badgingOutput -join "`n"
        $packageMatch = [regex]::Match($badgingText, "(?im)^package:\s+name='(?<package>[^']+)'\s+versionCode='(?<code>\d+)'\s+versionName='(?<name>[^']*)'")
        if ($badgingExitCode -eq 0 -and $packageMatch.Success) {
            $result.inspection_succeeded = $true
            $result.package_name = $packageMatch.Groups["package"].Value
            $result.version_code = [int]$packageMatch.Groups["code"].Value
            $result.version_name = $packageMatch.Groups["name"].Value
        }
        $global:LASTEXITCODE = 0
        if ((Split-Path -Leaf $ToolPath) -ieq "aapt2.exe") {
            $xmlOutput = & $ToolPath dump xmltree --file AndroidManifest.xml $ApkPath 2>&1 | ForEach-Object { [string]$_ }
        }
        else {
            $xmlOutput = & $ToolPath dump xmltree $ApkPath AndroidManifest.xml 2>&1 | ForEach-Object { [string]$_ }
        }
        $xmlExitCode = $LASTEXITCODE
        $hardening = Inspect-AaptXmlTreeHardening $xmlOutput
        $result.xmltree_inspection_succeeded = $xmlExitCode -eq 0 -and $hardening.inspection_succeeded
        foreach ($field in @(
            "debuggable_declared",
            "debuggable",
            "test_only_declared",
            "test_only",
            "allow_backup_declared",
            "allow_backup",
            "cleartext_traffic_declared",
            "uses_cleartext_traffic",
            "unsafe_exported_components",
            "component_count"
        )) {
            $result[$field] = Get-PropertyValue $hardening $field
        }
        if ($badgingText -match '(?im)^application-debuggable\b') {
            $result.debuggable_declared = $true
            $result.debuggable = $true
        }
        if ($badgingText -match '(?im)^application-testOnly\b') {
            $result.test_only_declared = $true
            $result.test_only = $true
        }
        $result.hardening_verified = (
            $result.xmltree_inspection_succeeded -and
            -not $result.debuggable -and
            -not $result.test_only -and
            $result.allow_backup_declared -and
            -not $result.allow_backup -and
            $result.cleartext_traffic_declared -and
            -not $result.uses_cleartext_traffic -and
            @($result.unsafe_exported_components).Count -eq 0
        )
    }
    catch {
        $result.inspection_succeeded = $false
    }
    return [pscustomobject]$result
}

if ($null -ne $easJson) {
    $build = Get-PropertyValue $easJson "build"
    $preview = Get-PropertyValue $build "preview"
    $production = Get-PropertyValue $build "production"
    $previewAndroid = Get-PropertyValue $preview "android"
    $productionAndroid = Get-PropertyValue $production "android"

    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue (Get-PropertyValue $easJson "cli") "version"))) {
        Add-Issue $sourceIssues "missing_eas_cli_version" "mobile/eas.json must pin a minimum EAS CLI version."
    }
    if ((Get-PropertyValue $preview "distribution") -ne "internal") {
        Add-Issue $sourceIssues "preview_not_internal" "EAS preview builds must use distribution=internal for QA APK collection."
    }
    if ((Get-PropertyValue $preview "channel") -ne "preview") {
        Add-Issue $sourceIssues "preview_channel_missing" "EAS preview builds must set channel=preview so QA artifacts are labeled separately from production."
    }
    if ((Get-PropertyValue $previewAndroid "buildType") -ne "apk") {
        Add-Issue $sourceIssues "preview_not_apk" "EAS preview Android profile must build an APK, not only an AAB."
    }
    if (-not (Test-BooleanFalse (Get-PropertyValue $preview "developmentClient"))) {
        Add-Issue $sourceIssues "preview_is_development_client" "The preview APK profile must explicitly set developmentClient=false so it is installable QA app evidence, not a development client."
    }
    if ((Get-PropertyValue $production "distribution") -ne "store") {
        Add-Issue $sourceIssues "production_not_store_distribution" "EAS production builds must set distribution=store for release distribution."
    }
    if ((Get-PropertyValue $production "channel") -ne "production") {
        Add-Issue $sourceIssues "production_channel_missing" "EAS production builds must set channel=production so production artifacts are labeled separately from QA."
    }
    if (-not (Test-BooleanFalse (Get-PropertyValue $production "developmentClient"))) {
        Add-Issue $sourceIssues "production_is_development_client" "The production Android profile must explicitly set developmentClient=false."
    }
    if ((Get-PropertyValue $productionAndroid "buildType") -ne "app-bundle") {
        Add-Issue $sourceIssues "production_not_app_bundle" "EAS production Android profile should build an app bundle for store distribution."
    }
    if (-not (Test-BooleanTrue (Get-PropertyValue $production "autoIncrement"))) {
        Add-Issue $sourceIssues "production_no_auto_increment" "EAS production Android profile should autoIncrement versionCode."
    }
}

if ($null -ne $mobilePackage) {
    $scripts = Get-PropertyValue $mobilePackage "scripts"
    $devDependencies = Get-PropertyValue $mobilePackage "devDependencies"
    foreach ($scriptName in @("typecheck", "smoke:token", "smoke:task-companion", "smoke:remote-input-grant", "smoke:android-prebuild-network-security", "smoke:android-manifest-resources", "smoke:android-lan-tls", "gate:android-instrumentation-compile", "gate:android-connected-lan-tls", "preflight:android-release", "build:android:preview", "build:android:production", "gate:android-release")) {
        if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $scripts $scriptName))) {
            Add-Issue $sourceIssues "missing_mobile_script" "mobile/package.json must define script '$scriptName'."
        }
    }
    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $devDependencies "eas-cli"))) {
        Add-Issue $sourceIssues "missing_mobile_eas_cli_dev_dependency" "mobile/package.json must include devDependency eas-cli so APK builds do not rely on a global EAS CLI install."
    }
    foreach ($dependencyGroupName in @("dependencies", "devDependencies")) {
        $dependencyGroup = Get-PropertyValue $mobilePackage $dependencyGroupName
        foreach ($dependency in @($dependencyGroup.PSObject.Properties)) {
            $version = [string]$dependency.Value
            if ($version -match '^[\^~<>=*]') {
                Add-Issue $sourceIssues "mobile_dependency_not_exact" "mobile/package.json $dependencyGroupName.$($dependency.Name) must use an exact version for reproducible release builds."
            }
        }
    }
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "preflight:android-release" @("verify_android_release_gate.ps1", "-PreflightOnly")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "build:android:preview" @("preflight:android-release", "eas build", "--platform android", "--profile preview", "--non-interactive")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "build:android:production" @("preflight:android-release", "eas build", "--platform android", "--profile production", "--non-interactive")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "gate:android-release" @("verify_android_release_gate.ps1")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "smoke:android-prebuild-network-security" @("android-prebuild-network-security-smoke.cjs")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "smoke:android-manifest-resources" @("android-manifest-resources-smoke.cjs")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "smoke:android-lan-tls" @("android-lan-tls-smoke.cjs")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "gate:android-instrumentation-compile" @("android-lan-tls-smoke.cjs", "--compile-instrumentation")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "gate:android-connected-lan-tls" @("android-lan-tls-smoke.cjs", "--connected")
}

if ($null -ne $rootPackage) {
    $scripts = Get-PropertyValue $rootPackage "scripts"
    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $scripts "android:release-gate"))) {
        Add-Issue $sourceIssues "missing_root_android_gate_script" "package.json must define android:release-gate."
    }
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "android:release-gate" @("verify_android_release_gate.ps1")
}

$artifactSummary = [ordered]@{
    provided = -not [string]::IsNullOrWhiteSpace($ArtifactPath)
    label = Get-SafeArtifactLabel $ArtifactPath
    sha256 = ""
    bytes = 0
    installable_apk = $false
    apk_zip_header_valid = $false
    apk_structure_valid = $false
    sdk_toolchain = [ordered]@{
        evaluated = $false
        test_only = $false
        trusted_root_label = ""
        expected_version = ""
        source_properties_version = ""
        source_properties_path = ""
        apksigner_sha256 = ""
        apksigner_jar_sha256 = ""
        aapt_sha256 = ""
        provenance_verified = $false
    }
    apk_signing = [ordered]@{
        evaluated = $false
        tool_label = ""
        command = "apksigner verify --verbose --print-certs"
        verification_succeeded = $false
        v2_verified = $false
        v3_verified = $false
        signer_count = 0
        signer_certificate_sha256 = ""
        expected_signer_certificate_sha256 = ""
        signer_identity_verified = $false
    }
    manifest_identity = [ordered]@{
        evaluated = $false
        tool_label = ""
        command = "aapt dump badging + dump xmltree AndroidManifest.xml"
        inspection_succeeded = $false
        xmltree_inspection_succeeded = $false
        package_name = ""
        version_name = ""
        version_code = 0
        matches_source_config = $false
        debuggable_declared = $false
        debuggable = $false
        test_only_declared = $false
        test_only = $false
        allow_backup_declared = $false
        allow_backup = $true
        cleartext_traffic_declared = $false
        uses_cleartext_traffic = $true
        unsafe_exported_components = @()
        component_count = 0
        hardening_verified = $false
    }
    provenance = [ordered]@{
        evaluated = $false
        evidence_bound = $false
        candidate_bound = $false
        identity_matches_apk = $false
        verified = $false
    }
}
$realDeviceEvidence = $null
$realDeviceEvidenceReview = $null
$realDeviceEvidenceDevice = $null
$realDeviceEvidenceTransport = $null
$realDeviceEvidenceCertificate = $null
$realDeviceEvidenceClaimControls = $null
$realDeviceEvidenceChecks = $null
$realDeviceEvidenceRedaction = $null
$realDeviceEvidenceApp = $null
$realDeviceEvidenceLabels = @()
$realDeviceEvidenceDeviceKind = ""
$realDeviceEvidenceSha = ""
$reviewedEvidenceContract = [ordered]@{
    evaluated = $false
    valid_hash = $false
    valid_signature = $false
    candidate_binding_valid = $false
    artifact_identity_valid = $false
    artifact_provenance_valid = $false
    artifact_manifest_valid = $false
    signing_key_fingerprint_bound = $false
}

if ($PreflightOnly) {
    Add-Issue $warnings "preflight_only" "Preflight mode checks source configuration only. It is not APK, install, WSS, or remote-control evidence." "warning"
    if (-not [string]::IsNullOrWhiteSpace($ArtifactPath)) {
        Add-Issue $warnings "preflight_ignores_artifact" "-PreflightOnly ignores -ArtifactPath. Rerun without -PreflightOnly for installable APK validation." "warning"
    }
    if (-not [string]::IsNullOrWhiteSpace($RealDeviceEvidencePath)) {
        Add-Issue $warnings "preflight_ignores_real_device_evidence" "-PreflightOnly ignores -RealDeviceEvidencePath. Rerun without -PreflightOnly for Android/emulator HTTPS/WSS evidence validation." "warning"
    }
}
else {
    if (-not $RequireCandidateBinding) {
        Add-Issue $artifactIssues "strict_candidate_binding_not_requested" "Strict Android release validation requires -RequireCandidateBinding so reviewed provenance cannot be replayed across candidates."
    }
    if ([string]::IsNullOrWhiteSpace($ArtifactPath)) {
        Add-Issue $artifactIssues "missing_android_artifact" "Strict Android release gate requires -ArtifactPath pointing to the QA APK under test."
    }
    elseif (-not (Test-Path -LiteralPath $ArtifactPath)) {
        Add-Issue $artifactIssues "android_artifact_not_found" "Android artifact path does not exist: $(Get-DisplayPath $ArtifactPath)"
    }
    else {
        $resolvedArtifact = (Resolve-Path -LiteralPath $ArtifactPath).Path
        $artifactItem = Get-Item -LiteralPath $resolvedArtifact
        $extension = $artifactItem.Extension.ToLowerInvariant()
        $artifactSummary.label = Redact-DisplayLabel $artifactItem.Name
        $artifactSummary.bytes = $artifactItem.Length
        $artifactSummary.sha256 = Get-Sha256Hex $resolvedArtifact
        $artifactSummary.apk_zip_header_valid = ($extension -eq ".apk" -and (Test-ApkZipHeader $resolvedArtifact))
        $artifactSummary.apk_structure_valid = ($extension -eq ".apk" -and $artifactSummary.apk_zip_header_valid -and (Test-ApkZipStructure $resolvedArtifact))
        $artifactSummary.installable_apk = ($extension -eq ".apk" -and $artifactSummary.apk_zip_header_valid -and $artifactSummary.apk_structure_valid)

        if ($extension -ne ".apk") {
            Add-Issue $artifactIssues "artifact_not_installable_apk" "The Android artifact must be an installable .apk for real-device QA. AAB/store bundles are not direct install evidence."
        }
        elseif (-not $artifactSummary.apk_zip_header_valid) {
            Add-Issue $artifactIssues "artifact_not_apk_zip" "The Android artifact must have an APK/ZIP file header, not just a .apk filename."
        }
        elseif (-not $artifactSummary.apk_structure_valid) {
            Add-Issue $artifactIssues "artifact_missing_apk_entries" "The Android APK must contain AndroidManifest.xml binary XML and classes.dex entries."
        }
        if ($artifactItem.Length -lt 1048576) {
            Add-Issue $artifactIssues "artifact_too_small" "The Android APK is smaller than 1 MiB; this looks like a placeholder, not an installable app artifact."
        }

        $controlledSignerInput = $ExpectedSignerCertificateSha256
        if ([string]::IsNullOrWhiteSpace($controlledSignerInput)) {
            $controlledSignerInput = [Environment]::GetEnvironmentVariable("LENGRVIS_ANDROID_RELEASE_CERTIFICATE_SHA256")
        }
        $controlledSignerSha256 = ConvertTo-Sha256Hex $controlledSignerInput
        $artifactSummary.apk_signing.expected_signer_certificate_sha256 = $controlledSignerSha256
        if ([string]::IsNullOrWhiteSpace($controlledSignerInput)) {
            Add-Issue $artifactIssues "missing_android_release_certificate_sha256" "Strict Android release validation requires -ExpectedSignerCertificateSha256 or LENGRVIS_ANDROID_RELEASE_CERTIFICATE_SHA256 from the protected release identity."
        }
        elseif ([string]::IsNullOrWhiteSpace($controlledSignerSha256)) {
            Add-Issue $artifactIssues "invalid_android_release_certificate_sha256" "The controlled Android release certificate SHA-256 must be exactly 64 hexadecimal characters, with optional separators."
        }

        $androidBuildTools = Resolve-TrustedAndroidBuildTools $artifactIssues
        $artifactSummary.sdk_toolchain = [ordered]@{
            evaluated = $androidBuildTools.evaluated
            test_only = $androidBuildTools.test_only
            trusted_root_label = $androidBuildTools.trusted_root_label
            expected_version = $androidBuildTools.expected_version
            source_properties_version = $androidBuildTools.source_properties_version
            source_properties_path = $androidBuildTools.source_properties_path
            apksigner_sha256 = $androidBuildTools.apksigner_sha256
            apksigner_jar_sha256 = $androidBuildTools.apksigner_jar_sha256
            aapt_sha256 = $androidBuildTools.aapt_sha256
            provenance_verified = $androidBuildTools.provenance_verified
        }
        $resolvedApkSignerPath = [string]$androidBuildTools.apksigner_path
        if ([string]::IsNullOrWhiteSpace($resolvedApkSignerPath)) {
            Add-Issue $artifactIssues "android_apksigner_not_found" "Approved Android SDK apksigner.bat is required for strict APK signature verification."
        }
        else {
            $signatureResult = Invoke-ApkSignerVerification $resolvedApkSignerPath $resolvedArtifact
            $signerMatches = (
                -not [string]::IsNullOrWhiteSpace($controlledSignerSha256) -and
                $signatureResult.signer_certificate_sha256 -eq $controlledSignerSha256
            )
            $artifactSummary.apk_signing = [ordered]@{
                evaluated = $signatureResult.evaluated
                tool_label = $signatureResult.tool_label
                command = $signatureResult.command
                verification_succeeded = $signatureResult.verification_succeeded
                v2_verified = $signatureResult.v2_verified
                v3_verified = $signatureResult.v3_verified
                signer_count = $signatureResult.signer_count
                signer_certificate_sha256 = $signatureResult.signer_certificate_sha256
                expected_signer_certificate_sha256 = $controlledSignerSha256
                signer_identity_verified = $signerMatches
            }
            if (-not $signatureResult.verification_succeeded) {
                Add-Issue $artifactIssues "android_apk_signature_invalid" "Android SDK apksigner verify --verbose --print-certs did not validate the supplied APK."
            }
            if (-not $signatureResult.v2_verified) {
                Add-Issue $artifactIssues "android_apk_v2_signature_missing" "The supplied APK must verify with APK Signature Scheme v2."
            }
            if (-not $signatureResult.v3_verified) {
                Add-Issue $artifactIssues "android_apk_v3_signature_missing" "The supplied APK must verify with APK Signature Scheme v3."
            }
            if ($signatureResult.signer_count -ne 1) {
                Add-Issue $artifactIssues "android_apk_signer_count_invalid" "The supplied APK must have exactly one reviewed signer."
            }
            if ([string]::IsNullOrWhiteSpace($signatureResult.signer_certificate_sha256)) {
                Add-Issue $artifactIssues "android_apk_signer_digest_missing" "apksigner output must include one signer certificate SHA-256 digest."
            }
            elseif (-not $signerMatches) {
                Add-Issue $artifactIssues "android_apk_signer_certificate_mismatch" "The APK signer certificate SHA-256 does not match the protected Android release identity."
            }
        }

        $resolvedAaptPath = [string]$androidBuildTools.aapt_path
        if ([string]::IsNullOrWhiteSpace($resolvedAaptPath)) {
            Add-Issue $artifactIssues "android_aapt_not_found" "Approved Android SDK aapt2.exe is required to inspect the final APK manifest."
        }
        else {
            $manifestResult = Invoke-ApkManifestInspection $resolvedAaptPath $resolvedArtifact
            $manifestMatchesSource = (
                $manifestResult.inspection_succeeded -and
                $manifestResult.package_name -eq $expectedAndroidPackageName -and
                $manifestResult.version_name -eq $expectedAndroidVersionName -and
                $manifestResult.version_code -eq $expectedAndroidVersionCode
            )
            $artifactSummary.manifest_identity = [ordered]@{
                evaluated = $manifestResult.evaluated
                tool_label = $manifestResult.tool_label
                command = $manifestResult.command
                inspection_succeeded = $manifestResult.inspection_succeeded
                xmltree_inspection_succeeded = $manifestResult.xmltree_inspection_succeeded
                package_name = $manifestResult.package_name
                version_name = $manifestResult.version_name
                version_code = $manifestResult.version_code
                matches_source_config = $manifestMatchesSource
                debuggable_declared = $manifestResult.debuggable_declared
                debuggable = $manifestResult.debuggable
                test_only_declared = $manifestResult.test_only_declared
                test_only = $manifestResult.test_only
                allow_backup_declared = $manifestResult.allow_backup_declared
                allow_backup = $manifestResult.allow_backup
                cleartext_traffic_declared = $manifestResult.cleartext_traffic_declared
                uses_cleartext_traffic = $manifestResult.uses_cleartext_traffic
                unsafe_exported_components = @($manifestResult.unsafe_exported_components)
                component_count = $manifestResult.component_count
                hardening_verified = $manifestResult.hardening_verified
            }
            if (-not $manifestResult.inspection_succeeded) {
                Add-Issue $artifactIssues "android_apk_manifest_inspection_failed" "Android SDK aapt dump badging could not extract package/version identity from the supplied APK."
            }
            elseif (-not $manifestMatchesSource) {
                Add-Issue $artifactIssues "android_apk_manifest_identity_mismatch" "The APK package name, version name, and version code must match mobile/app.json."
            }
            if (-not $manifestResult.xmltree_inspection_succeeded) {
                Add-Issue $artifactIssues "android_apk_manifest_xmltree_failed" "Android SDK aapt must inspect the final binary AndroidManifest.xml, not only source configuration or badging."
            }
            if ($manifestResult.debuggable) {
                Add-Issue $artifactIssues "android_apk_debuggable" "The final APK manifest must not set android:debuggable=true."
            }
            if ($manifestResult.test_only) {
                Add-Issue $artifactIssues "android_apk_test_only" "The final APK manifest must not set android:testOnly=true."
            }
            if (-not $manifestResult.allow_backup_declared -or $manifestResult.allow_backup) {
                Add-Issue $artifactIssues "android_apk_allow_backup_not_disabled" "The final APK manifest must explicitly set android:allowBackup=false."
            }
            if (-not $manifestResult.cleartext_traffic_declared -or $manifestResult.uses_cleartext_traffic) {
                Add-Issue $artifactIssues "android_apk_cleartext_traffic_not_disabled" "The final APK manifest must explicitly set android:usesCleartextTraffic=false."
            }
            if (@($manifestResult.unsafe_exported_components).Count -gt 0) {
                Add-Issue $artifactIssues "android_apk_unsafe_exported_component" "The final APK contains exported components that are neither the launcher nor protected by an Android permission."
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($RealDeviceEvidencePath)) {
        Add-Issue $deviceIssues "missing_real_device_evidence" "Strict Android release gate requires -RealDeviceEvidencePath with reviewed phone/emulator HTTPS/WSS remote-control evidence."
    }
    elseif (-not (Test-Path -LiteralPath $RealDeviceEvidencePath)) {
        Add-Issue $deviceIssues "real_device_evidence_not_found" "Real-device evidence path does not exist: $(Get-DisplayPath $RealDeviceEvidencePath)"
    }
    else {
        $realDevice = Read-JsonFile $RealDeviceEvidencePath "android_real_device_evidence" $deviceIssues
        if ($null -ne $realDevice) {
            $reviewedEvidenceContract.evaluated = $true
            $python = Get-Command python -ErrorAction SilentlyContinue
            if ($null -eq $python) {
                Add-Issue $deviceIssues "android_reviewed_evidence_contract_invalid" "Python is required to verify the sealed Android reviewed-evidence contract."
            }
            else {
                $pythonArgs = @(
                    (Join-Path $PSScriptRoot "verify_android_reviewed_evidence.py"),
                    "--evidence",
                    $RealDeviceEvidencePath
                )
                if ($RequireCandidateBinding) {
                    $pythonArgs += "--require-candidate-binding"
                }
                $verifierOutput = & $python.Source @pythonArgs 2>&1 | Out-String
                $verifierExitCode = $LASTEXITCODE
                $verifierResult = $null
                try {
                    $verifierResult = $verifierOutput | ConvertFrom-Json
                }
                catch {
                }
                if ($null -ne $verifierResult) {
                    $contract = Get-PropertyValue $verifierResult "contract"
                    $reviewedEvidenceContract.valid_hash = Test-BooleanTrue (Get-PropertyValue $contract "valid_hash")
                    $reviewedEvidenceContract.valid_signature = Test-BooleanTrue (Get-PropertyValue $contract "valid_signature")
                    $reviewedEvidenceContract.candidate_binding_valid = Test-BooleanTrue (Get-PropertyValue $contract "candidate_binding_valid")
                    $reviewedEvidenceContract.artifact_identity_valid = Test-BooleanTrue (Get-PropertyValue $contract "artifact_identity_valid")
                    $reviewedEvidenceContract.artifact_provenance_valid = Test-BooleanTrue (Get-PropertyValue $contract "artifact_provenance_valid")
                    $reviewedEvidenceContract.artifact_manifest_valid = Test-BooleanTrue (Get-PropertyValue $contract "artifact_manifest_valid")
                    $reviewedEvidenceContract.signing_key_fingerprint_bound = Test-BooleanTrue (Get-PropertyValue $contract "signing_key_fingerprint_bound")
                }
                if ($verifierExitCode -ne 0 -or $null -eq $verifierResult -or -not (Test-BooleanTrue (Get-PropertyValue $verifierResult "ok"))) {
                    Add-Issue $deviceIssues "android_reviewed_evidence_contract_invalid" "Android reviewed evidence must be sealed with a valid release-evidence HMAC contract."
                }
                elseif ($RequireCandidateBinding -and -not $reviewedEvidenceContract.candidate_binding_valid) {
                    Add-Issue $deviceIssues "android_reviewed_evidence_contract_invalid" "Android reviewed evidence must match the strict release candidate identity."
                }
                if (-not $reviewedEvidenceContract.artifact_identity_valid) {
                    Add-Issue $artifactIssues "android_reviewed_artifact_identity_invalid" "Signed Android reviewed evidence must contain a valid APK digest, package/version identity, and signer certificate digest."
                }
                if (-not $reviewedEvidenceContract.artifact_provenance_valid) {
                    Add-Issue $artifactIssues "android_artifact_provenance_invalid" "Signed Android reviewed evidence must contain a candidate-bound reviewed build provenance record."
                }
                if (-not $reviewedEvidenceContract.artifact_manifest_valid) {
                    Add-Issue $artifactIssues "android_artifact_manifest_invalid" "Signed Android reviewed evidence must bind required redacted artifacts by SHA-256 and byte size."
                }
                if (-not $reviewedEvidenceContract.signing_key_fingerprint_bound) {
                    Add-Issue $artifactIssues "android_evidence_signing_key_unbound" "Signed Android reviewed evidence must bind its signing-key fingerprint label inside the signature payload."
                }
            }
            if ((Get-PropertyValue $realDevice "artifact_type") -ne "android-real-device-remote-control-evidence") {
                Add-Issue $deviceIssues "invalid_real_device_artifact_type" "Real-device evidence JSON must use artifact_type=android-real-device-remote-control-evidence."
            }
            if ((Get-PropertyValue $realDevice "real_device_result") -ne "passed") {
                Add-Issue $deviceIssues "real_device_result_not_passed" "Real-device evidence must record real_device_result=passed."
            }

            $review = Get-PropertyValue $realDevice "review"
            $reviewStatus = Get-ReviewStatus $realDevice
            if ($reviewStatus -ne "reviewed_passed") {
                Add-Issue $deviceIssues "review_status_not_passed" "Real-device evidence must record review.status=reviewed_passed or top-level review_status=reviewed_passed."
            }
            if (-not (Test-RedactedFreeText (Get-ReviewerLabel $realDevice))) {
                Add-Issue $deviceIssues "reviewer_label_missing" "Real-device evidence must include a non-placeholder redacted reviewer label."
            }
            if (-not (Test-UtcTimestamp (Get-ReviewedAtUtc $realDevice))) {
                Add-Issue $deviceIssues "reviewed_at_utc_missing" "Real-device evidence must include review.reviewed_at_utc as a UTC timestamp."
            }
            if (-not (Test-BooleanTrue (Get-PropertyValue $review "redaction_reviewed"))) {
                Add-Issue $deviceIssues "review_redaction_not_confirmed" "Real-device evidence review.redaction_reviewed must be the JSON boolean true."
            }
            if (-not (Test-BooleanTrue (Get-PropertyValue $review "evidence_artifacts_reviewed"))) {
                Add-Issue $deviceIssues "review_artifacts_not_confirmed" "Real-device evidence review.evidence_artifacts_reviewed must be the JSON boolean true."
            }

            $device = Get-PropertyValue $realDevice "device"
            $deviceKind = [string](Get-PropertyValue $device "kind")
            if ($deviceKind -notin @("android_phone", "android_emulator")) {
                Add-Issue $deviceIssues "device_kind_invalid" "Real-device evidence device.kind must be android_phone or android_emulator."
            }
            Add-RequiredRedactedValueIssue $deviceIssues "device_label_missing" "device.profile_label_redacted" (Get-PropertyValue $device "profile_label_redacted")

            $transport = Get-PropertyValue $realDevice "transport"
            if (-not (Test-RedactedHttpsOrigin (Get-PropertyValue $transport "https_origin_redacted"))) {
                Add-Issue $deviceIssues "https_origin_redacted_missing" "Real-device evidence transport.https_origin_redacted must be https://[redacted-host] with an optional port."
            }
            foreach ($transportField in @("approval_wss_origin_redacted", "remote_screen_wss_origin_redacted", "remote_input_wss_origin_redacted")) {
                if (-not (Test-RedactedWssUrl (Get-PropertyValue $transport $transportField))) {
                    Add-Issue $deviceIssues "wss_origin_redacted_missing" "Real-device evidence transport.$transportField must be a wss://[redacted-host]/... URL label."
                }
            }

            $certificate = Get-PropertyValue $realDevice "certificate"
            $hasTrustLabel = Test-RedactedFreeText (Get-PropertyValue $certificate "trust_path_label_redacted")
            $hasFingerprintLabel = Test-RedactedFreeText (Get-PropertyValue $certificate "fingerprint_label_redacted")
            if (-not ($hasTrustLabel -or $hasFingerprintLabel)) {
                Add-Issue $deviceIssues "certificate_trust_label_missing" "Real-device evidence must include certificate.trust_path_label_redacted or certificate.fingerprint_label_redacted."
            }

            $evidenceLabels = @((Get-PropertyValue $realDevice "evidence_artifacts_redacted") | Where-Object {
                $null -ne $_ -and -not [string]::IsNullOrWhiteSpace([string]$_)
            })
            if ($evidenceLabels.Count -eq 0) {
                Add-Issue $deviceIssues "evidence_artifact_labels_missing" "Real-device evidence must include at least one reviewed redacted artifact label in evidence_artifacts_redacted."
            }
            foreach ($evidenceLabel in $evidenceLabels) {
                Add-RequiredRedactedValueIssue $deviceIssues "evidence_artifact_label_not_redacted" "evidence_artifacts_redacted[]" $evidenceLabel
            }

            $claimControls = Get-PropertyValue $realDevice "claim_controls"
            foreach ($flag in @(
                "apk_installed",
                "camera_qr_pairing_verified",
                "https_api_reachability_verified",
                "https_wss_verified",
                "certificate_trust_verified",
                "approval_wss_verified",
                "remote_screen_verified",
                "remote_input_verified",
                "revoke_expiry_verified",
                "artifact_redaction_reviewed",
                "real_device_pass_claim_allowed"
            )) {
                if (-not (Test-BooleanTrue (Get-PropertyValue $claimControls $flag))) {
                    Add-Issue $deviceIssues "real_device_claim_flag_missing" "Real-device evidence claim_controls.$flag must be true."
                }
            }

            $checks = Get-PropertyValue $realDevice "checks"
            foreach ($checkName in @(
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
            )) {
                if (-not (Test-StatusPassed (Get-PropertyValue $checks $checkName))) {
                    Add-Issue $deviceIssues "real_device_check_not_passed" "Real-device evidence checks.$checkName must be passed."
                }
            }

            $redaction = Get-PropertyValue $realDevice "redaction"
            foreach ($redactionFlag in @(
                "tokens_absent",
                "pairing_codes_absent",
                "raw_hosts_absent",
                "raw_device_ids_absent",
                "raw_grant_ids_absent",
                "private_paths_absent"
            )) {
                if (-not (Test-BooleanTrue (Get-PropertyValue $redaction $redactionFlag))) {
                    Add-Issue $deviceIssues "redaction_flag_missing" "Real-device evidence redaction.$redactionFlag must be true."
                }
            }

            $evidenceApp = Get-PropertyValue $realDevice "app"
            $evidenceSha = [string](Get-PropertyValue $evidenceApp "artifact_sha256")
            Add-RequiredRedactedValueIssue $deviceIssues "app_artifact_label_missing" "app.artifact_label_redacted" (Get-PropertyValue $evidenceApp "artifact_label_redacted")
            Add-RequiredRedactedValueIssue $deviceIssues "eas_build_label_missing" "app.eas_build_label_redacted" (Get-PropertyValue $evidenceApp "eas_build_label_redacted")
            if ([string](Get-PropertyValue $evidenceApp "build_profile") -ne "preview") {
                Add-Issue $deviceIssues "build_profile_not_preview" "Real-device evidence app.build_profile must be preview, matching the internal QA APK profile."
            }
            if ($evidenceSha -notmatch "^[a-fA-F0-9]{64}$") {
                Add-Issue $deviceIssues "artifact_hash_missing" "Real-device evidence app.artifact_sha256 must be a 64-character SHA-256 hex digest."
            }
            if (-not [string]::IsNullOrWhiteSpace($artifactSummary.sha256) -and $evidenceSha.ToLowerInvariant() -ne $artifactSummary.sha256) {
                Add-Issue $deviceIssues "artifact_hash_mismatch" "Real-device evidence app.artifact_sha256 must match the APK supplied to this gate."
            }
            $evidencePackageName = [string](Get-PropertyValue $evidenceApp "package_name")
            $evidenceVersionName = [string](Get-PropertyValue $evidenceApp "version_name")
            $evidenceVersionCode = Get-PropertyValue $evidenceApp "version_code"
            $evidenceSignerSha256 = ConvertTo-Sha256Hex (Get-PropertyValue $evidenceApp "signer_certificate_sha256")
            $artifactIdentityMatchesApk = (
                $evidenceSha -match "^[a-fA-F0-9]{64}$" -and
                $evidenceSha.ToLowerInvariant() -eq $artifactSummary.sha256 -and
                $evidencePackageName -eq $artifactSummary.manifest_identity.package_name -and
                $evidenceVersionName -eq $artifactSummary.manifest_identity.version_name -and
                ($evidenceVersionCode -is [int] -or $evidenceVersionCode -is [long]) -and
                [int]$evidenceVersionCode -eq [int]$artifactSummary.manifest_identity.version_code -and
                -not [string]::IsNullOrWhiteSpace($evidenceSignerSha256) -and
                $evidenceSignerSha256 -eq $artifactSummary.apk_signing.signer_certificate_sha256
            )
            if (-not $artifactIdentityMatchesApk) {
                Add-Issue $artifactIssues "android_reviewed_identity_mismatch" "Signed Android reviewed evidence package/version, APK digest, and signer certificate must match Android SDK inspection of the supplied APK."
            }
            $artifactSummary.provenance = [ordered]@{
                evaluated = $true
                evidence_bound = ($reviewedEvidenceContract.valid_hash -and $reviewedEvidenceContract.valid_signature)
                candidate_bound = $reviewedEvidenceContract.candidate_binding_valid
                identity_matches_apk = $artifactIdentityMatchesApk
                verified = (
                    $reviewedEvidenceContract.valid_hash -and
                    $reviewedEvidenceContract.valid_signature -and
                    $reviewedEvidenceContract.candidate_binding_valid -and
                    $reviewedEvidenceContract.artifact_identity_valid -and
                    $reviewedEvidenceContract.artifact_provenance_valid -and
                    $reviewedEvidenceContract.artifact_manifest_valid -and
                    $reviewedEvidenceContract.signing_key_fingerprint_bound -and
                    $artifactIdentityMatchesApk
                )
            }
        }
    }
}

if (-not $PreflightOnly -and -not $artifactSummary.provenance.verified) {
    Add-Issue $artifactIssues "android_artifact_provenance_not_verified" "Strict Android release validation requires signed, candidate-bound artifact provenance matching the inspected APK."
}

$sourcePassed = $sourceIssues.Count -eq 0
$artifactEvaluated = -not $PreflightOnly
$deviceEvaluated = -not $PreflightOnly
$artifactPassed = $artifactEvaluated -and $artifactIssues.Count -eq 0
$devicePassed = $deviceEvaluated -and $deviceIssues.Count -eq 0
$releaseReady = (-not $PreflightOnly) -and $sourcePassed -and $artifactPassed -and $devicePassed
$status = if ($releaseReady) { "passed" } elseif ($sourcePassed -and $PreflightOnly) { "preflight_ready_not_release" } else { "blocked" }
$recommendedCommands = @(
    [ordered]@{
        purpose = "source_config_preflight"
        command = "npm run android:release-gate -- -PreflightOnly"
        claim_scope = "Source/config readiness only; not APK, install, WSS, or release evidence."
    },
    [ordered]@{
        purpose = "mobile_preflight_alias"
        command = "npm --prefix mobile run preflight:android-release"
        claim_scope = "Same source/config readiness check from the mobile package."
    },
    [ordered]@{
        purpose = "prebuild_network_security_smoke"
        command = "npm --prefix mobile run smoke:android-prebuild-network-security"
        claim_scope = "Confirms Expo prebuild keeps the main Android network-security config fail-closed while loopback exceptions remain debug-only."
    },
    [ordered]@{
        purpose = "qa_apk_build"
        command = "npm --prefix mobile run build:android:preview"
        claim_scope = "Runs preflight first, then requests an EAS internal APK build. The build log is still required."
    },
    [ordered]@{
        purpose = "store_bundle_build"
        command = "npm --prefix mobile run build:android:production"
        claim_scope = "Runs preflight first, then requests an EAS production AAB build. This does not submit or publish."
    },
    [ordered]@{
        purpose = "real_device_template"
        command = "npm run evidence:android-real-device-template -- -ArtifactLabel ""<redacted apk label>"" -ArtifactSha256 ""<sha256>"" -DeviceLabel ""<redacted device label>"" -BackendBuildLabel ""<redacted backend/build label>"""
        claim_scope = "Fail-closed evidence starting point only; not a pass."
    },
    [ordered]@{
        purpose = "connected_lan_tls_gate"
        command = "npm --prefix mobile run gate:android-connected-lan-tls"
        claim_scope = "Release/evidence-only connected Android HTTPS/WSS regression; useful support evidence, not a substitute for reviewed screenshots/logs and strict gate JSON."
    },
    [ordered]@{
        purpose = "strict_gate"
        command = "npm run android:release-gate -- -ArtifactPath ""<qa apk path>"" -RealDeviceEvidencePath ""<sealed android evidence json>"" -ExpectedSignerCertificateSha256 ""<controlled release certificate sha256>"" -AndroidBuildToolsRoot ""<approved build-tools/version root>"" -ExpectedBuildToolsVersion ""<version>"" -ExpectedApkSignerSha256 ""<sha256>"" -ExpectedApkSignerJarSha256 ""<sha256>"" -ExpectedAaptSha256 ""<sha256>"" -RequireCandidateBinding"
        claim_scope = "Strict approved Android SDK provenance, signature, final merged-manifest, package/version, sealed candidate provenance, and Android/emulator evidence gate; still not Play Store publication evidence."
    }
)
$mustNotClaim = @()
$nextSteps = @("Attach this gate output to the release packet.")
$failureClosure = @(
    "Record this gate output path and exact command in the RC handoff.",
    "Do not claim Play Store submission, Play Store publication, or public release from this gate output."
)
if (-not $releaseReady) {
    $mustNotClaim = @(
        "installable Android app release pass",
        "real-device Android remote-control pass",
        "LAN HTTPS/WSS mobile pass",
        "Play Store submission or publication",
        "release-candidate mobile signoff"
    )
    $nextSteps = @(
        "Build the EAS preview APK and pass it with -ArtifactPath.",
        "Install that APK on the target Android device or emulator.",
        "Collect reviewed HTTPS/WSS QR, remote screen, remote input, revoke, expiry, and redaction evidence.",
        "Rerun this script without -PreflightOnly using the APK and real-device evidence JSON."
    )
    $failureClosure = @(
        "Keep the status as blocked or preflight_ready_not_release in the RC handoff.",
        "Capture the EAS build URL/log, redacted project/build label, APK file label, and APK SHA-256.",
        "Record the controlled Android release certificate SHA-256 and reviewed builder invocation in the sealed evidence; never infer signer identity from the APK alone.",
        "Install the exact APK on the target Android phone/emulator and collect reviewed camera QR, HTTPS/WSS, certificate trust, remote screen/input, revoke/expiry, and redaction artifacts.",
        "When a configured Android device/emulator and LAN TLS backend are available, run npm --prefix mobile run gate:android-connected-lan-tls and attach its redacted command/log note as supporting trust-path evidence.",
        "Fill the Android real-device evidence JSON only after manual review marks every required check passed.",
        "Rerun the strict gate with the APK/evidence, controlled signer digest, approved build-tools root/version/tool digests, and -RequireCandidateBinding; only an exit 0 can close the Android APK/real-device gate.",
        "Use a separate EAS submit/Play Console record and release-owner approval before any submitted or published claim."
    )
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $resolvedRoot ".tmp\android-release-gate"
}
$runId = "run-{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
$runRoot = Join-Path $OutputRoot $runId
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$summary = [ordered]@{
    artifact_type = "android-release-gate-summary"
    generated_by = "scripts/verify_android_release_gate.ps1"
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    status = $status
    release_ready = $releaseReady
    published_to_store = $false
    preflight_only = [bool]$PreflightOnly
    mode_scope = if ($PreflightOnly) { "source/config preflight only; APK and real-device evidence are not evaluated" } else { "strict APK plus reviewed Android/emulator evidence gate" }
    strict_evidence_contract = $strictEvidenceContract
    recommended_commands = $recommendedCommands
    source_config = [ordered]@{
        passed = $sourcePassed
        issues = @($sourceIssues.ToArray())
    }
    android_artifact = $artifactSummary
    artifact_gate = [ordered]@{
        evaluated = $artifactEvaluated
        passed = $artifactPassed
        issues = @($artifactIssues.ToArray())
    }
    real_device_gate = [ordered]@{
        evaluated = $deviceEvaluated
        passed = $devicePassed
        evidence_label = Get-SafeArtifactLabel $RealDeviceEvidencePath
        issues = @($deviceIssues.ToArray())
    }
    reviewed_evidence_contract = $reviewedEvidenceContract
    warnings = @($warnings.ToArray())
    claim_controls = [ordered]@{
        installable_android_app_claim_allowed = $releaseReady
        real_device_remote_control_claim_allowed = $releaseReady
        expo_preview_is_not_release = $true
        preflight_ready_is_release_pass = $false
        strict_gate_required_for_release_claim = $true
        strict_gate_pass_is_store_submission = $false
        store_submission_verified = $false
        store_publication_claim_allowed = $false
        requires_reviewed_apk_install_evidence = $true
        requires_reviewed_https_wss_remote_control_evidence = $true
    }
    must_not_claim = $mustNotClaim
    next_steps = $nextSteps
    failure_closure = $failureClosure
}

$jsonPath = Join-Path $runRoot "android-release-gate.redacted.json"
$mdPath = Join-Path $runRoot "android-release-gate.redacted.md"
$summary | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 -LiteralPath $jsonPath

$markdown = New-Object System.Collections.Generic.List[string]
$markdown.Add("# Android Release Gate")
$markdown.Add("")
$markdown.Add("- Status: $status")
$markdown.Add("- Release ready: $releaseReady")
$markdown.Add("- Published to store: False")
$markdown.Add("- Preflight only: $([bool]$PreflightOnly)")
$markdown.Add("- Mode scope: $($summary.mode_scope)")
$markdown.Add("- APK label: $($artifactSummary.label)")
$markdown.Add("- APK SHA-256: $($artifactSummary.sha256)")
$markdown.Add("- APK signer certificate SHA-256: $($artifactSummary.apk_signing.signer_certificate_sha256)")
$markdown.Add("- APK Signature Scheme v2 verified: $($artifactSummary.apk_signing.v2_verified)")
$markdown.Add("- APK Signature Scheme v3 verified: $($artifactSummary.apk_signing.v3_verified)")
$markdown.Add("- Android SDK build-tools provenance verified: $($artifactSummary.sdk_toolchain.provenance_verified)")
$markdown.Add("- APK package/version: $($artifactSummary.manifest_identity.package_name) $($artifactSummary.manifest_identity.version_name) ($($artifactSummary.manifest_identity.version_code))")
$markdown.Add("- Final APK manifest hardening verified: $($artifactSummary.manifest_identity.hardening_verified)")
$markdown.Add("- Candidate-bound artifact provenance verified: $($artifactSummary.provenance.verified)")
$markdown.Add("- Real-device evidence label: $(Get-SafeArtifactLabel $RealDeviceEvidencePath)")
$markdown.Add("")
$markdown.Add("## Source Config")
if ($sourcePassed) {
    $markdown.Add("- Passed")
}
else {
    foreach ($issue in $sourceIssues) {
        $markdown.Add("- [$($issue.code)] $($issue.message)")
    }
}
$markdown.Add("")
$markdown.Add("## Artifact Gate")
if ($PreflightOnly) {
    $markdown.Add("- Not evaluated in PreflightOnly mode. This is source/config evidence only.")
}
elseif ($artifactPassed) {
    $markdown.Add("- Passed")
}
else {
    foreach ($issue in $artifactIssues) {
        $markdown.Add("- [$($issue.code)] $($issue.message)")
    }
}
$markdown.Add("")
$markdown.Add("## Real-Device Gate")
if ($PreflightOnly) {
    $markdown.Add("- Not evaluated in PreflightOnly mode. This is not Android/emulator HTTPS/WSS evidence.")
}
elseif ($devicePassed) {
    $markdown.Add("- Passed")
}
else {
    foreach ($issue in $deviceIssues) {
        $markdown.Add("- [$($issue.code)] $($issue.message)")
    }
}
$markdown.Add("")
$markdown.Add("## Strict Evidence JSON Contract")
$markdown.Add("- artifact_type: $($strictEvidenceContract.artifact_type)")
$markdown.Add("- review.status or review_status: $($strictEvidenceContract.review_status)")
$markdown.Add("- reviewer fields: $($strictEvidenceContract.reviewer_fields -join ', ')")
$markdown.Add("- app fields: $($strictEvidenceContract.app_fields -join ', ')")
$markdown.Add("- APK signature: $($strictEvidenceContract.apk_signature)")
$markdown.Add("- Android SDK toolchain: $($strictEvidenceContract.sdk_toolchain)")
$markdown.Add("- Merged manifest: $($strictEvidenceContract.merged_manifest)")
$markdown.Add("- Artifact provenance: $($strictEvidenceContract.artifact_provenance)")
$markdown.Add("- device fields: $($strictEvidenceContract.device_fields -join ', ')")
$markdown.Add("- transport fields: $($strictEvidenceContract.transport_fields -join ', ')")
$markdown.Add("- evidence labels: $($strictEvidenceContract.evidence_labels)")
$markdown.Add("")
$markdown.Add("## Recommended Commands")
foreach ($recommendedCommand in $recommendedCommands) {
    $markdown.Add("- $($recommendedCommand.purpose): ``$($recommendedCommand.command)``")
    $markdown.Add("  - Scope: $($recommendedCommand.claim_scope)")
}
$markdown.Add("")
$markdown.Add("## Failure Closure")
foreach ($closureStep in $failureClosure) {
    $markdown.Add("- $closureStep")
}
$markdown.Add("")
$markdown.Add("## Claim Controls")
$markdown.Add("- installable_android_app_claim_allowed: $releaseReady")
$markdown.Add("- real_device_remote_control_claim_allowed: $releaseReady")
$markdown.Add("- preflight_ready_is_release_pass: False")
$markdown.Add("- strict_gate_required_for_release_claim: True")
$markdown.Add("- store_submission_verified: False")
$markdown.Add("- store_publication_claim_allowed: False")
$markdown.Add("- A passed strict gate proves only the supplied QA APK plus reviewed Android/emulator evidence; it does not prove EAS submit, Play Console review, rollout, or publication.")
$markdown.Add("- This file is redacted gate evidence; it does not contain tokens, raw LAN hosts, raw device ids, or raw grant ids.")
$markdown | Set-Content -Encoding UTF8 -LiteralPath $mdPath

Write-Host "Android release gate status: $status"
Write-Host "Redacted summary: $(Get-DisplayPath $jsonPath)"
Write-Host "Redacted markdown: $(Get-DisplayPath $mdPath)"

if (-not $releaseReady) {
    if ($PreflightOnly -and $sourcePassed) {
        Write-Host "[ready] Android source configuration is ready for APK and real-device evidence collection." -ForegroundColor Green
        Write-Host "This is not an installable APK pass or real-device remote-control pass." -ForegroundColor Yellow
        exit 0
    }

    Write-Host "[blocked] Android release gate is missing required APK and/or real-device evidence:" -ForegroundColor Red
    $allBlockingIssues = @($sourceIssues.ToArray()) + @($artifactIssues.ToArray()) + @($deviceIssues.ToArray())
    foreach ($issue in $allBlockingIssues) {
        Write-Host " - [$($issue.code)] $($issue.message)" -ForegroundColor Red
    }
    exit 1
}

Write-Host "[passed] Android APK signature, package/version, controlled signer, candidate-bound provenance, and real-device evidence are verified."
