[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$ArtifactLabel = "",
    [string]$ArtifactSha256 = "",
    [string]$DeviceLabel = "",
    [string]$BackendBuildLabel = "",
    [string]$CandidateCommit = "",
    [string]$CandidateBuildIdentifier = "",
    [string]$CandidateRepository = "",
    [string]$CandidateRunId = "",
    [string]$CandidateRunAttempt = "",
    [string]$SignerCertificateSha256 = "",
    [string]$BuilderId = "",
    [string]$BuildInvocationId = "",
    [string]$BuiltAtUtc = "",
    [string]$BlockedReason = "uncollected"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
if ([string]::IsNullOrWhiteSpace($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $resolvedRoot ".tmp\android-real-device-evidence-template"
}
elseif (-not [System.IO.Path]::IsPathRooted($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $resolvedRoot $EvidenceRoot
}

function Protect-Label {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return "uncollected"
    }
    $text = $Value.Trim()
    $text = [regex]::Replace($text, "(?i)\bhttps?://[^/\s\\]+", "https://[redacted-host]")
    $text = [regex]::Replace($text, "(?i)\bwss?://[^/\s\\]+", "wss://[redacted-host]")
    $text = [regex]::Replace($text, "\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-host]")
    $text = [regex]::Replace($text, "(?i)\b(?:token|secret|password|pairing[_ -]?code|otp)\s*[:=]\s*[A-Za-z0-9._~+/=-]+", "[redacted-sensitive]")
    $text = [regex]::Replace($text, "(?i)\b(?:deviceId|device_id|grantId|grant_id)\s*[:=]\s*[A-Za-z0-9._~:/-]+", "[redacted-id]")
    $text = [regex]::Replace($text, "[A-Za-z]:\\[^\s,;]+", "[redacted-path]")
    # POSIX absolute paths (CI runners on Linux/macOS) are not drive-letter
    # rooted; redact temp/home roots so private filesystem paths never leak into
    # shared evidence artifacts the way Windows C:\ paths are already scrubbed.
    $text = [regex]::Replace($text, "(?:/private)?(?:/var/folders|/var/tmp|/tmp|/Users/[^/\s,;]+|/home/[^/\s,;]+|/root)(?:/[^\s,;]*)?", "[redacted-path]")
    return $text
}

function Get-DisplayPath {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return ""
    }

    try {
        $fullPath = [System.IO.Path]::GetFullPath($PathValue)
        $rootPrefix = $resolvedRoot.TrimEnd([char[]]@("\", "/")) + [System.IO.Path]::DirectorySeparatorChar
        if ($fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return (Protect-Label ($fullPath.Substring($rootPrefix.Length)))
        }

        return (Protect-Label (Split-Path -Leaf $fullPath))
    }
    catch {
        return (Protect-Label (Split-Path -Leaf $PathValue))
    }
}

function New-UncollectedCheck {
    param(
        [string]$Why,
        [string]$RequiredEvidence = "",
        [string]$OverclaimGuard = ""
    )

    if ([string]::IsNullOrWhiteSpace($RequiredEvidence)) {
        $RequiredEvidence = $Why
    }
    if ([string]::IsNullOrWhiteSpace($OverclaimGuard)) {
        $OverclaimGuard = "Do not mark passed until reviewed Android phone/emulator artifacts are attached."
    }

    return [ordered]@{
        status = "uncollected"
        artifact_label = "uncollected"
        required_evidence = Protect-Label $RequiredEvidence
        overclaim_guard = Protect-Label $OverclaimGuard
        reviewer_note = Protect-Label $Why
    }
}

function Test-CommandAvailable {
    param([string]$Name)

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-EnvValuePresent {
    param([string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name)
    return -not [string]::IsNullOrWhiteSpace($value)
}

function Read-MobileEasCliVersion {
    $packagePath = Join-Path $resolvedRoot "mobile\package.json"
    if (-not (Test-Path -LiteralPath $packagePath)) {
        return "uncollected"
    }

    try {
        $packageJson = Get-Content -Raw -Encoding UTF8 -LiteralPath $packagePath | ConvertFrom-Json
        $version = [string]$packageJson.devDependencies."eas-cli"
        if ([string]::IsNullOrWhiteSpace($version)) {
            return "uncollected"
        }
        return Protect-Label $version
    }
    catch {
        return "uncollected"
    }
}

function Read-MobileAppIdentity {
    $appPath = Join-Path $resolvedRoot "mobile\app.json"
    if (-not (Test-Path -LiteralPath $appPath)) {
        return $null
    }

    try {
        $appJson = Get-Content -Raw -Encoding UTF8 -LiteralPath $appPath | ConvertFrom-Json
        return [ordered]@{
            package_name = [string]$appJson.expo.android.package
            version_name = [string]$appJson.expo.version
            version_code = $appJson.expo.android.versionCode
        }
    }
    catch {
        return $null
    }
}

$runId = "run-{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
$runRoot = Join-Path $EvidenceRoot $runId
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$blocked = Protect-Label $BlockedReason
$easCliVersion = Read-MobileEasCliVersion
$mobileAppIdentity = Read-MobileAppIdentity
$artifactShaNormalized = if ($ArtifactSha256 -match "^[a-fA-F0-9]{64}$") { $ArtifactSha256.ToLowerInvariant() } else { "uncollected" }
$signerShaNormalized = if ($SignerCertificateSha256 -match "^[a-fA-F0-9]{64}$") { $SignerCertificateSha256.ToLowerInvariant() } else { "uncollected" }
$packageName = if ($null -ne $mobileAppIdentity -and -not [string]::IsNullOrWhiteSpace([string]$mobileAppIdentity.package_name)) { [string]$mobileAppIdentity.package_name } else { "uncollected" }
$versionName = if ($null -ne $mobileAppIdentity -and -not [string]::IsNullOrWhiteSpace([string]$mobileAppIdentity.version_name)) { [string]$mobileAppIdentity.version_name } else { "uncollected" }
$versionCode = "uncollected"
if ($null -ne $mobileAppIdentity) {
    $rawVersionCode = $mobileAppIdentity.version_code
    if (($rawVersionCode -is [int] -or $rawVersionCode -is [long]) -and
        [long]$rawVersionCode -ge 1 -and [long]$rawVersionCode -le [int]::MaxValue) {
        $versionCode = [int]$rawVersionCode
    }
}
$localEasCliBinaryPresent = (
    (Test-Path -LiteralPath (Join-Path $resolvedRoot "mobile\node_modules\.bin\eas.cmd")) -or
    (Test-Path -LiteralPath (Join-Path $resolvedRoot "mobile\node_modules\.bin\eas"))
)
$javaAvailable = Test-CommandAvailable "java"
$adbAvailable = Test-CommandAvailable "adb"
$gradleAvailable = Test-CommandAvailable "gradle"
$androidSdkEnvPresent = (Test-EnvValuePresent "ANDROID_HOME") -or (Test-EnvValuePresent "ANDROID_SDK_ROOT")
$nativeAndroidProjectPresent = Test-Path -LiteralPath (Join-Path $resolvedRoot "mobile\android")
$expoTokenPresent = Test-EnvValuePresent "EXPO_TOKEN"
$localApkBuildReady = $javaAvailable -and $androidSdkEnvPresent -and $nativeAndroidProjectPresent
$easCloudBuildPrereqsPresent = ($easCliVersion -ne "uncollected") -and $localEasCliBinaryPresent
$buildBlockerSummary = if ($expoTokenPresent) {
    "EAS cloud build auth may be available through EXPO_TOKEN; run the preview build and attach the redacted EAS build label/log."
}
elseif ($easCloudBuildPrereqsPresent) {
    "EAS preview APK build still requires eas login or EXPO_TOKEN before artifact creation."
}
elseif (-not $localApkBuildReady) {
    "Local APK build environment is incomplete; install Android SDK or use EAS with credentials."
}
else {
    "Build prerequisites need manual verification before claiming APK evidence."
}
$packet = [ordered]@{
    artifact_type = "android-real-device-remote-control-evidence"
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    template_status = "manual_real_device_evidence_required"
    strict_gate_contract = [ordered]@{
        verifier = "scripts/verify_android_release_gate.ps1"
        required_result = "real_device_result=passed"
        required_review = "review.status=reviewed_passed with reviewer_label, reviewed_at_utc, evidence_artifacts_reviewed=true, redaction_reviewed=true"
        required_identity_policy = "Use binding_ref or redacted active-grant labels in shareable artifacts; keep raw deviceId/grantId local-only."
        required_transport = "Redacted HTTPS origin plus approval, remote screen, and remote input WSS URLs."
        required_artifact_match = "APK SHA-256, package name, version code/name, and signer certificate SHA-256 must match Android SDK inspection of the APK supplied to android:release-gate -ArtifactPath."
        required_artifact_provenance = "app.provenance must bind the reviewed builder invocation, source commit/repository, APK digest, package/version, signer digest, and build timestamp."
        required_artifact_manifest = "evidence_artifact_manifest must bind redacted screenshot, video, backend/mobile log, and adb install-status labels to SHA-256 and byte size."
        required_candidate_binding = "Fill candidate.commit, build_identifier, repository, ci_run_id, and ci_run_attempt from the immutable reviewed candidate; seal with evidence:android-real-device-seal before strict release validation."
    }
    candidate = [ordered]@{
        commit = Protect-Label $CandidateCommit
        build_identifier = Protect-Label $CandidateBuildIdentifier
        repository = Protect-Label $CandidateRepository
        ci_run_id = Protect-Label $CandidateRunId
        ci_run_attempt = Protect-Label $CandidateRunAttempt
    }
    real_device_result = "uncollected"
    blocked_reason = $blocked
    build_environment = [ordered]@{
        java_available = $javaAvailable
        adb_available = $adbAvailable
        gradle_available = $gradleAvailable
        android_sdk_env_present = $androidSdkEnvPresent
        native_android_project_present = $nativeAndroidProjectPresent
        local_apk_build_ready = $localApkBuildReady
        local_eas_cli_declared = $easCliVersion -ne "uncollected"
        local_eas_cli_declared_version = $easCliVersion
        local_eas_cli_binary_present = $localEasCliBinaryPresent
        expo_token_present = $expoTokenPresent
        eas_cloud_auth_verified = $false
        eas_cloud_auth_verification = "not_checked_by_template; run npm --prefix mobile exec eas -- whoami --non-interactive or set EXPO_TOKEN"
        build_blocker_summary = Protect-Label $buildBlockerSummary
    }
    app = [ordered]@{
        artifact_label = Protect-Label $ArtifactLabel
        artifact_label_redacted = Protect-Label $ArtifactLabel
        artifact_sha256 = $artifactShaNormalized
        build_profile = "preview"
        eas_build_label_redacted = Protect-Label $BuildInvocationId
        package_name = $packageName
        version_name = $versionName
        version_code = $versionCode
        signer_certificate_sha256 = $signerShaNormalized
        provenance = [ordered]@{
            type = "reviewed-build-record/v1"
            builder_id = Protect-Label $BuilderId
            build_invocation_id = Protect-Label $BuildInvocationId
            source_repository = Protect-Label $CandidateRepository
            source_commit = Protect-Label $CandidateCommit
            build_profile = "preview"
            built_at_utc = Protect-Label $BuiltAtUtc
            artifact_sha256 = $artifactShaNormalized
            package_name = $packageName
            version_name = $versionName
            version_code = $versionCode
            signer_certificate_sha256 = $signerShaNormalized
        }
    }
    device = [ordered]@{
        kind = "uncollected"
        profile_label_redacted = Protect-Label $DeviceLabel
        label = Protect-Label $DeviceLabel
        backend_build_label = Protect-Label $BackendBuildLabel
    }
    transport = [ordered]@{
        https_origin_redacted = "uncollected"
        approval_wss_origin_redacted = "uncollected"
        remote_screen_wss_origin_redacted = "uncollected"
        remote_input_wss_origin_redacted = "uncollected"
    }
    certificate = [ordered]@{
        trust_path_label_redacted = "uncollected"
        fingerprint_label_redacted = "uncollected"
        device_trust_verified = $false
    }
    review = [ordered]@{
        status = "unreviewed"
        reviewer_label = "uncollected"
        reviewed_at_utc = "uncollected"
        evidence_artifacts_reviewed = $false
        redaction_reviewed = $false
    }
    evidence = [ordered]@{
        payload_sha256 = ""
        signature = ""
        signature_payload_version = "reviewed-evidence-ed25519/v3"
        signing_key_fingerprint = ""
    }
    evidence_artifact_manifest = [ordered]@{
        version = "sha256-manifest/v1"
        entries = @()
    }
    evidence_artifacts_redacted = @()
    claim_controls = [ordered]@{
        apk_installed = $false
        camera_qr_pairing_verified = $false
        https_api_reachability_verified = $false
        https_wss_verified = $false
        certificate_trust_verified = $false
        approval_wss_verified = $false
        remote_screen_verified = $false
        remote_input_verified = $false
        click_input_verified = $false
        text_input_verified = $false
        key_pagedown_verified = $false
        mobile_stop_readonly_verified = $false
        desktop_revoke_readonly_verified = $false
        grant_expiry_readonly_verified = $false
        revoke_expiry_verified = $false
        binding_ref_used_for_shareable_artifacts = $false
        raw_device_grant_ids_local_only = $false
        artifact_redaction_reviewed = $false
        real_device_pass_claim_allowed = $false
    }
    checks = [ordered]@{
        apk_installed = New-UncollectedCheck "Install the exact APK on the target Android device or emulator." "APK install screenshot or device package listing tied to the APK SHA-256 under test." "Source config, Expo preview metadata, or APK existence is not install evidence."
        camera_qr_pairing = New-UncollectedCheck "Pair using camera QR or documented emulator scan path." "Redacted phone/emulator scan proof plus paired state." "Source QR generation, parser smoke, paste/manual entry, or preflight output is not camera QR evidence."
        https_api_reachability = New-UncollectedCheck "Show HTTPS API reachability from the Android device with token-bearing HTTP blocked." "Device-originated HTTPS health/pairing reachability and blocked non-loopback HTTP token flow evidence." "LAN preflight readiness is not device HTTPS evidence."
        certificate_trust_path = New-UncollectedCheck "Record certificate source/fingerprint or CA plus explicit Android/emulator trust path." "Certificate fingerprint/CA label and Android/emulator trust settings or expected trust-failure proof." "Cert/key parsing or desktop trust alone is not Android device trust evidence."
        approval_wss = New-UncollectedCheck "Show approval WebSocket connected over WSS from the device." "Mobile approval received over /ws/mobile/approvals WSS plus approve and reject outcome evidence." "HTTPS API reachability does not prove approval WSS."
        remote_screen_wss = New-UncollectedCheck "Show remote screen frames over WSS from the device." "Visible remote frame, connection state, transport notice, and read-only state over /ws/remote/screen WSS." "Approval WSS does not prove remote screen WSS."
        remote_input_wss = New-UncollectedCheck "Show remote input WebSocket connected over WSS from the device." "Grant-scoped /ws/remote/input WSS connection, remaining time, active grant label, and disabled/read-only state before/after grant." "Remote screen WSS does not prove remote input WSS."
        click_input_approval = New-UncollectedCheck "Send benign click input and record desktop approval." "Mobile click action plus desktop approval/dry-run record for the active grant." "Client/source smoke is not real-device click evidence."
        text_input_approval = New-UncollectedCheck "Send benign text input and record desktop approval." "Mobile text input plus desktop approval/dry-run record for the active grant." "Do not include typed secrets or raw values in shared artifacts."
        key_pagedown_approval = New-UncollectedCheck "Send PageDown key input and record desktop approval." "Mobile PageDown key input plus desktop approval/dry-run record for the active grant." "Generic key support is not PageDown acceptance evidence."
        mobile_end_control_readonly = New-UncollectedCheck "End control from mobile and prove read-only fallback." "Mobile end-control action, input disabled/read-only state, and closed or rejected input channel." "A revoke API unit test is not mobile UI stop evidence."
        desktop_revoke_readonly = New-UncollectedCheck "Revoke from desktop and prove input cannot continue." "Desktop/device-list revoke plus mobile revoked/disconnected/read-only state." "Mobile-side stop does not prove desktop revoke."
        grant_expiry_readonly = New-UncollectedCheck "Let grant expire and prove input cannot continue." "Short TTL or waited expiry with remaining-time expired/disabled state and stale input rejected." "Revoke evidence does not prove expiry behavior."
        background_or_lockscreen_privacy = New-UncollectedCheck "Record background/lockscreen behavior without task/token leakage." "Background/lockscreen screenshots or notes showing safe pause/disconnect and notification redaction." "Foreground-only evidence does not prove background privacy."
        artifact_redaction_review = New-UncollectedCheck "Review screenshots/logs before sharing." "Reviewer note confirming no tokens, pairing codes, raw hosts, raw device ids, raw grant ids, private paths, selectors, nested args, support-only notes, or task secrets." "Use public binding_ref or redacted active-grant labels in shared artifacts; raw device and grant identifiers stay local-only."
    }
    redaction = [ordered]@{
        tokens_absent = $false
        grant_tokens_absent = $false
        pairing_codes_absent = $false
        raw_qr_payloads_absent = $false
        raw_hosts_absent = $false
        raw_device_ids_absent = $false
        raw_grant_ids_absent = $false
        device_names_absent = $false
        private_paths_absent = $false
        selectors_absent = $false
        nested_model_action_args_absent = $false
        support_only_notes_absent = $false
        binding_ref_or_redacted_active_grant_label_used = $false
    }
    required_real_device_scope = @(
        "APK installed on the target Android phone/emulator",
        "camera QR pairing or documented emulator scan path",
        "HTTPS API and WSS trust on that device",
        "remote screen frames over WSS",
        "remote input click/text/PageDown approvals",
        "mobile end-control returns to read-only",
        "desktop/device revoke returns to read-only",
        "grant expiry returns to read-only",
        "shareable artifacts use binding_ref/redacted labels, never raw deviceId/grantId"
    )
    shareable_identity_policy = [ordered]@{
        public_remote_input_correlation = "binding_ref or redacted active-grant label only"
        raw_device_id_storage = "local-only reproduction notes outside tracked/shared artifacts"
        raw_grant_id_storage = "local-only reproduction notes outside tracked/shared artifacts"
    }
    must_not_claim = @(
        "installable Android app release pass",
        "real-device Android remote-control pass",
        "LAN HTTPS/WSS mobile pass",
        "release-candidate mobile signoff"
    )
}

$jsonPath = Join-Path $runRoot "android-real-device-evidence.redacted.template.json"
$mdPath = Join-Path $runRoot "android-real-device-evidence.redacted.template.md"
$packet | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 -LiteralPath $jsonPath

$markdown = @(
    "# Android Real-Device Evidence Template",
    "",
    "- Result: uncollected",
    "- Claim allowed: false",
    "- APK label: $($packet.app.artifact_label_redacted)",
    "- Device label: $($packet.device.profile_label_redacted)",
    "- Blocked reason: $blocked",
    "- Build blocker: $($packet.build_environment.build_blocker_summary)",
    "- Local APK build ready: $($packet.build_environment.local_apk_build_ready)",
    "- EAS CLI declared: $($packet.build_environment.local_eas_cli_declared)",
    "- EXPO_TOKEN present: $($packet.build_environment.expo_token_present)",
    "",
    "Fill this template only from reviewed Android/emulator evidence. It is not a pass until every claim-control flag and required check is true/passed and redaction review is complete.",
    "",
    "## Strict Gate Contract",
    "",
    "- `real_device_result` must be `passed`.",
    "- `review.status` must be `reviewed_passed`, with reviewer, UTC timestamp, artifact review, and redaction review recorded.",
    "- `app.artifact_sha256`, package/version, and signer certificate SHA-256 must match Android SDK inspection of the exact APK supplied to `android:release-gate -ArtifactPath`.",
    "- `app.provenance` must bind the reviewed builder invocation and timestamp to the candidate source plus the same APK digest, package/version, and signer digest.",
    "- `evidence_artifact_manifest` must include SHA-256 and byte size for redacted device screenshot/video, backend/mobile logs, and adb install-status evidence; labels must match `evidence_artifacts_redacted`.",
    "- Fill all `candidate` fields from the immutable candidate context, then run `npm run evidence:android-real-device-seal` with the reviewer-only Ed25519 private key.",
    "- A template has no valid signature and can never satisfy the strict gate.",
    "- `transport` must contain redacted HTTPS plus approval/screen/input WSS labels.",
    "- Shareable artifacts must use `binding_ref` or redacted active-grant labels; raw `deviceId` and `grantId` stay local-only.",
    "",
    "## Required Real-Device Evidence",
    "",
    "- [ ] APK installed on the target Android phone/emulator.",
    "- [ ] Camera QR pairing or documented emulator scan path.",
    "- [ ] HTTPS API reachability and approval/screen/input WSS from the device, with explicit certificate trust path.",
    "- [ ] Remote screen frames render over WSS and stay read-only by default.",
    "- [ ] Remote input click, text, and PageDown each create the expected desktop approval/dry-run record.",
    "- [ ] Mobile end-control returns the session to read-only/no-input.",
    "- [ ] Desktop/device revoke returns the mobile UI to read-only/no-input.",
    "- [ ] Grant expiry returns the mobile UI to read-only/no-input and stale input cannot reconnect.",
    "- [ ] Shareable artifacts use binding_ref or redacted active-grant labels, never raw deviceId/grantId.",
    "- [ ] Redaction review confirms no tokens, pairing codes, raw QR payloads, raw hosts, raw device ids, raw grant ids, private paths, selectors, nested model-action args, support-only notes, or task secrets."
) -join "`n"
$markdown | Set-Content -Encoding UTF8 -LiteralPath $mdPath

Write-Host "Android real-device evidence template created:"
Write-Host " - $(Get-DisplayPath $jsonPath)"
Write-Host " - $(Get-DisplayPath $mdPath)"
Write-Host "This template is fail-closed and is not a real-device pass."
