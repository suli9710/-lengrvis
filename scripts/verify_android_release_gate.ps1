[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$ArtifactPath = "",
    [string]$RealDeviceEvidencePath = "",
    [string]$OutputRoot = "",
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$script:ResolvedRootForDisplay = ""

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
    app_fields = @("app.artifact_sha256", "app.artifact_label_redacted", "app.build_profile", "app.eas_build_label_redacted")
    device_fields = @("device.kind", "device.profile_label_redacted")
    transport_fields = @(
        "transport.https_origin_redacted",
        "transport.approval_wss_origin_redacted",
        "transport.remote_screen_wss_origin_redacted",
        "transport.remote_input_wss_origin_redacted"
    )
    evidence_labels = "At least one reviewed redacted screenshot/video/log label in evidence_artifacts_redacted."
    sensitive_values = "No raw tokens, pairing codes, hosts/IPs, device ids, grant ids, or private paths in shareable labels."
}

$appJsonPath = Join-Path $mobileRoot "app.json"
$easJsonPath = Join-Path $mobileRoot "eas.json"
$mobilePackagePath = Join-Path $mobileRoot "package.json"
$rootPackagePath = Join-Path $resolvedRoot "package.json"

$appJson = Read-JsonFile $appJsonPath "mobile_app_json" $sourceIssues
$easJson = Read-JsonFile $easJsonPath "mobile_eas_json" $sourceIssues
$mobilePackage = Read-JsonFile $mobilePackagePath "mobile_package_json" $sourceIssues
$rootPackage = Read-JsonFile $rootPackagePath "root_package_json" $sourceIssues

if ($null -ne $appJson) {
    $expo = Get-PropertyValue $appJson "expo"
    $android = Get-PropertyValue $expo "android"
    $plugins = Get-PropertyValue $expo "plugins"
    $easProjectId = Get-PropertyValue (Get-PropertyValue (Get-PropertyValue $expo "extra") "eas") "projectId"

    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $expo "name"))) {
        Add-Issue $sourceIssues "missing_app_name" "mobile/app.json must define expo.name for an installable Android artifact."
    }
    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $expo "slug"))) {
        Add-Issue $sourceIssues "missing_app_slug" "mobile/app.json must define expo.slug."
    }
    if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $expo "version"))) {
        Add-Issue $sourceIssues "missing_app_version" "mobile/app.json must define expo.version."
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
        'certificates src="user"',
        'cleartextTrafficPermitted="false"',
        "android:networkSecurityConfig",
        "android:usesCleartextTraffic",
        "WindowManager.LayoutParams.FLAG_SECURE"
    )) {
        if ($hardeningPluginSource.IndexOf($fragment, [System.StringComparison]::Ordinal) -lt 0) {
            Add-Issue $sourceIssues "android_hardening_plugin_contract_mismatch" "Android hardening plugin must include '$fragment' for LAN TLS trust and remote-screen screenshot protection."
        }
    }
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
    foreach ($scriptName in @("typecheck", "smoke:token", "smoke:task-companion", "smoke:remote-input-grant", "preflight:android-release", "build:android:preview", "build:android:production", "gate:android-release")) {
        if ([string]::IsNullOrWhiteSpace((Get-PropertyValue $scripts $scriptName))) {
            Add-Issue $sourceIssues "missing_mobile_script" "mobile/package.json must define script '$scriptName'."
        }
    }
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "preflight:android-release" @("verify_android_release_gate.ps1", "-PreflightOnly")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "build:android:preview" @("preflight:android-release", "eas build", "--platform android", "--profile preview", "--non-interactive")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "build:android:production" @("preflight:android-release", "eas build", "--platform android", "--profile production", "--non-interactive")
    Add-RequiredScriptFragmentIssue $sourceIssues $scripts "gate:android-release" @("verify_android_release_gate.ps1")
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
        $artifactSummary.sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedArtifact).Hash.ToLowerInvariant()
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
        }
    }
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
        purpose = "strict_gate"
        command = "npm run android:release-gate -- -ArtifactPath ""<qa apk path>"" -RealDeviceEvidencePath ""<reviewed android evidence json>"""
        claim_scope = "Strict APK plus reviewed Android/emulator evidence gate; still not Play Store publication evidence."
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
        "Install the exact APK on the target Android phone/emulator and collect reviewed camera QR, HTTPS/WSS, certificate trust, remote screen/input, revoke/expiry, and redaction artifacts.",
        "Fill the Android real-device evidence JSON only after manual review marks every required check passed.",
        "Rerun the strict gate with both -ArtifactPath and -RealDeviceEvidencePath; only an exit 0 from that strict run can close the Android APK/real-device gate.",
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

Write-Host "[passed] Android APK and real-device remote-control evidence are present."
