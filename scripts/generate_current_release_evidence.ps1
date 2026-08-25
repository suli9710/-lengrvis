[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$OutputPath = "",
    [string]$CommitSha = "",
    [string]$GeneratedAtUtc = "",
    [string]$NeedsJson = "",
    [string]$ReleaseVersion = "",
    [string]$BuildIdentifier = "",
    [string]$ReleaseOwner = "",
    [string]$OwnerSignature = "",
    [string]$ManualSignoffStatus = "",
    [switch]$StrictReleaseSignoff,
    [string[]]$Waiver = @(),
    [string[]]$ManualAcceptance = @(),
    [string[]]$ArtifactLink = @()
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

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $resolvedRoot "docs\release\current-release-evidence.md"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $resolvedRoot $OutputPath
}
$resolvedOutputPath = [System.IO.Path]::GetFullPath($OutputPath)

function Get-GitValue([string[]]$Arguments, [string]$Fallback) {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        return $Fallback
    }

    Push-Location $resolvedRoot
    try {
        $value = & $git.Source @Arguments 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($value)) {
            return ([string]$value).Trim()
        }
    }
    catch {
    }
    finally {
        Pop-Location
    }

    return $Fallback
}

function Get-GitStatusSnapshot() {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        return @{
            available = $false
            lines = @()
        }
    }

    Push-Location $resolvedRoot
    try {
        $output = & $git.Source status --porcelain 2>$null
        if ($LASTEXITCODE -ne 0) {
            return @{
                available = $false
                lines = @()
            }
        }
        return @{
            available = $true
            lines = @($output | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
        }
    }
    catch {
        return @{
            available = $false
            lines = @()
        }
    }
    finally {
        Pop-Location
    }
}

function Get-FirstNonEmpty([string[]]$Values, [string]$Fallback) {
    foreach ($value in $Values) {
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
    }
    return $Fallback
}

function Get-PackageJsonValue([string]$RelativePath, [string]$PropertyName, [string]$Fallback) {
    $path = Join-Path $resolvedRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        return $Fallback
    }

    try {
        $package = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $property = $package.PSObject.Properties[$PropertyName]
        if ($null -ne $property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
            return ([string]$property.Value).Trim()
        }
    }
    catch {
    }

    return $Fallback
}

function Get-ToolVersion([string]$CommandName, [string[]]$Arguments) {
    $command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return "not found"
    }

    try {
        $output = & $command.Source @Arguments 2>&1
        if ($LASTEXITCODE -ne 0 -and $output.Count -eq 0) {
            return "error"
        }

        $line = @($output | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -First 1)
        if ($line.Count -gt 0) {
            return ([string]$line[0]).Trim()
        }
    }
    catch {
        return "error"
    }

    return "unknown"
}

function ConvertTo-NeedsResultMap([string]$Json) {
    $map = @{}
    if ([string]::IsNullOrWhiteSpace($Json)) {
        return $map
    }

    try {
        $needs = $Json | ConvertFrom-Json
        foreach ($property in $needs.PSObject.Properties) {
            $result = ""
            if ($null -ne $property.Value -and $null -ne $property.Value.PSObject.Properties["result"]) {
                $result = [string]$property.Value.result
            }
            if ($null -ne $property.Value -and
                $null -ne $property.Value.PSObject.Properties["outputs"] -and
                $null -ne $property.Value.outputs.PSObject.Properties["release_evidence_status"] -and
                -not [string]::IsNullOrWhiteSpace([string]$property.Value.outputs.release_evidence_status)) {
                $result = [string]$property.Value.outputs.release_evidence_status
            }
            $map[$property.Name] = $result
        }
    }
    catch {
        $map["needs-json-parse-error"] = "failure"
    }

    return $map
}

function Add-EnvListItems([System.Collections.Generic.List[string]]$Target, [string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return
    }

    foreach ($item in ($value -split "\r?\n|;;")) {
        $trimmed = ([string]$item).Trim()
        if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
            $Target.Add($trimmed)
        }
    }
}

function Escape-MarkdownCell([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }
    return ($Value -replace "\|", "\|") -replace "`r?`n", "<br>"
}

function Add-ListSection([System.Collections.Generic.List[string]]$Lines, [string]$Heading, [string[]]$Items, [string]$EmptyText) {
    $Lines.Add("## $Heading")
    $Lines.Add("")
    if ($Items.Count -eq 0) {
        $Lines.Add("- $EmptyText")
    }
    else {
        foreach ($item in $Items) {
            $Lines.Add("- $item")
        }
    }
    $Lines.Add("")
}

if ([string]::IsNullOrWhiteSpace($GeneratedAtUtc)) {
    $GeneratedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
}
if ([string]::IsNullOrWhiteSpace($CommitSha)) {
    $CommitSha = Get-FirstNonEmpty @(
        $env:LENGRVIS_RELEASE_CANDIDATE_COMMIT,
        $env:GITHUB_SHA,
        (Get-GitValue @("rev-parse", "HEAD") "unknown")
    ) "unknown"
}
if ([string]::IsNullOrWhiteSpace($NeedsJson)) {
    $NeedsJson = $env:RELEASE_EVIDENCE_NEEDS_JSON
}
if ([string]::IsNullOrWhiteSpace($ReleaseVersion)) {
    $ReleaseVersion = Get-FirstNonEmpty @(
        $env:RELEASE_VERSION,
        (Get-PackageJsonValue "package.json" "version" "unknown")
    ) "unknown"
}
$releaseVersionLabel = if ([string]::IsNullOrWhiteSpace($ReleaseVersion) -or $ReleaseVersion -eq "unknown") {
    "unknown"
}
elseif ($ReleaseVersion.Trim().StartsWith("v")) {
    $ReleaseVersion.Trim()
}
else {
    "v$($ReleaseVersion.Trim())"
}
if ([string]::IsNullOrWhiteSpace($BuildIdentifier)) {
    $BuildIdentifier = Get-FirstNonEmpty @(
        $env:LENGRVIS_RELEASE_BUILD_IDENTIFIER,
        $env:RELEASE_BUILD_IDENTIFIER,
        $(if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_RUN_ID)) {
            "$(Get-FirstNonEmpty @($env:GITHUB_WORKFLOW, 'CI') 'CI') $($env:GITHUB_RUN_ID)"
        }),
        $(if ($releaseVersionLabel -ne "unknown") { "$releaseVersionLabel local/manual" })
    ) "local/manual"
}
if ([string]::IsNullOrWhiteSpace($ReleaseOwner)) {
    $ReleaseOwner = Get-FirstNonEmpty @($env:RELEASE_OWNER, $env:GITHUB_ACTOR, [System.Environment]::UserName) "unknown"
}
if ([string]::IsNullOrWhiteSpace($OwnerSignature)) {
    $OwnerSignature = Get-FirstNonEmpty @(
        $env:RELEASE_OWNER_SIGNATURE,
        "PENDING_RELEASE_OWNER_SIGNATURE"
    ) "PENDING_RELEASE_OWNER_SIGNATURE"
}

$gitStatusSnapshot = Get-GitStatusSnapshot
$gitStatusLines = @($gitStatusSnapshot.lines)
$worktreeStatus = if (-not [bool]$gitStatusSnapshot.available) {
    "unavailable"
}
elseif ($gitStatusLines.Count -eq 0) {
    "clean"
}
else {
    "dirty"
}
$worktreeDirtyCount = $gitStatusLines.Count

$hasCandidateRunIdentity = -not [string]::IsNullOrWhiteSpace($env:LENGRVIS_RELEASE_CANDIDATE_RUN_ID)
$boundRunId = if ($hasCandidateRunIdentity) {
    $env:LENGRVIS_RELEASE_CANDIDATE_RUN_ID.Trim()
}
else {
    Get-FirstNonEmpty @($env:GITHUB_RUN_ID) "local/manual"
}
$boundRunAttempt = if ($hasCandidateRunIdentity) {
    Get-FirstNonEmpty @($env:LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT) "missing"
}
else {
    Get-FirstNonEmpty @($env:GITHUB_RUN_ATTEMPT) "local/manual"
}
$boundWorkflow = if ($hasCandidateRunIdentity) {
    "release-candidate"
}
else {
    Get-FirstNonEmpty @($env:GITHUB_WORKFLOW, "local/manual") "local/manual"
}
$runUrl = ""
if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_SERVER_URL) -and
    -not [string]::IsNullOrWhiteSpace($env:GITHUB_REPOSITORY) -and
    $boundRunId -match '^[1-9][0-9]*$') {
    $runUrl = "$($env:GITHUB_SERVER_URL)/$($env:GITHUB_REPOSITORY)/actions/runs/$boundRunId"
}

$artifactLinks = New-Object System.Collections.Generic.List[string]
foreach ($item in $ArtifactLink) {
    if (-not [string]::IsNullOrWhiteSpace($item)) {
        $artifactLinks.Add($item.Trim())
    }
}
Add-EnvListItems $artifactLinks "RELEASE_EVIDENCE_ARTIFACT_LINKS"
if (-not [string]::IsNullOrWhiteSpace($runUrl)) {
    $artifactLinks.Add("CI run: $runUrl")
    $artifactLinks.Add("CI artifacts page: $runUrl#artifacts")
}
$artifactLinks.Add("Current release evidence artifact: current-release-evidence")
$artifactLinks.Add("Current SBOM artifact: current-sbom")
$artifactLinks.Add("Extension security gate artifact: extension-security-gate")

$waivers = New-Object System.Collections.Generic.List[string]
foreach ($item in $Waiver) {
    if (-not [string]::IsNullOrWhiteSpace($item)) {
        $waivers.Add($item.Trim())
    }
}
Add-EnvListItems $waivers "RELEASE_EVIDENCE_WAIVERS"

$manualItems = New-Object System.Collections.Generic.List[string]
foreach ($item in $ManualAcceptance) {
    if (-not [string]::IsNullOrWhiteSpace($item)) {
        $manualItems.Add($item.Trim())
    }
}
Add-EnvListItems $manualItems "RELEASE_EVIDENCE_MANUAL_ACCEPTANCE"
if ($manualItems.Count -eq 0) {
    @(
        "PENDING: first launch on the candidate artifact with backend health visible.",
        "PENDING: natural-language agent task loop produces a user-readable result or actionable failure.",
        "PENDING: task evidence/replay privacy review confirms redacted summaries only.",
        "PENDING: one reversible approval flow and one forbidden request are verified.",
        "PENDING: document QA citation flow is verified against disposable content.",
        "PENDING: local/hybrid model UX and any clean-machine local-model claim are reviewed.",
        "PENDING: Skill/App sample import or display is reviewed if included in the release claim.",
        "PENDING: mobile companion, LAN/WSS, certificate trust, and remote input are reviewed if claimed.",
        "PENDING: diagnostics export content is reviewed before any external sharing.",
        "PENDING: release owner reviews waivers, residual risks, artifacts, and signs this evidence."
    ) | ForEach-Object { $manualItems.Add($_) }
}

$gates = @(
    [ordered]@{
        id = "hygiene"
        job = "Repo hygiene + dependency locks + review scorecard"
        scope = "Repository hygiene, dependency lock consistency, maintainability anti-regrowth, and full-review scorecard consistency"
        commands = @(
            "npm run hygiene",
            "npm run deps:verify",
            "npm run maintainability:gate",
            "npm run review:scorecard"
        )
    },
    [ordered]@{
        id = "backend"
        job = "Backend pytest + golden task + MCP conformance gate"
        scope = "Backend pytest suite, golden task regression, and official MCP lifecycle/tools/SSE resume conformance"
        commands = @(
            "npm ci --ignore-scripts --engine-strict",
            "python -m pip install --require-hashes -r requirements-dev-lock.txt",
            "python -m playwright install chromium",
            "python -m pytest backend/tests -q --maxfail=1",
            "powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/run_golden_tasks.ps1",
            "npm run mcp:conformance"
        )
    },
    [ordered]@{
        id = "real-llm-quality"
        job = "Real LLM quality gate"
        scope = "Real-provider quality gate; skipped or missing credentials block release evidence"
        commands = @(
            "python scripts/run_real_llm_eval.py --quality-gate"
        )
    },
    [ordered]@{
        id = "desktop"
        job = "Desktop typecheck + audit + behavior smokes"
        scope = "Desktop audit, TypeScript typecheck, Vitest, and behavior smokes"
        commands = @(
            "npm --prefix desktop ci",
            "npm --prefix desktop audit --audit-level=high",
            "npm --prefix desktop run typecheck",
            "npm --prefix desktop test",
            "npm --prefix desktop exec playwright install chromium",
            "npm --prefix desktop run smoke"
        )
    },
    [ordered]@{
        id = "mobile"
        job = "Mobile typecheck + behavior smokes"
        scope = "Mobile audit, TypeScript typecheck, and behavior smokes"
        commands = @(
            "npm --prefix mobile ci",
            "cd mobile; npm exec expo -- install --check",
            "npm --prefix mobile run smoke:eas-cli-compat",
            "npm --prefix mobile audit --audit-level=high",
            "npm --prefix mobile run typecheck",
            "npm --prefix mobile run smoke:token",
            "npm --prefix mobile run smoke:consent",
            "npm --prefix mobile run smoke:session-lifecycle",
            "npm --prefix mobile run smoke:push-notifications",
            "npm --prefix mobile run smoke:push-subscription-lifecycle",
            "npm --prefix mobile run smoke:task-companion",
            "npm --prefix mobile run smoke:remote-input-grant",
            "npm --prefix mobile run smoke:wakeup-contract",
            "npm --prefix mobile run smoke:android-back",
            "npm --prefix mobile run smoke:approval-status-label",
            "npm --prefix mobile run smoke:android-hardening-plugin",
            "npm --prefix mobile run smoke:android-prebuild-network-security",
            "npm --prefix mobile run smoke:android-manifest-resources",
            "npm --prefix mobile run smoke:android-lan-tls",
            "cd mobile/android; .\gradlew.bat :app:assembleDebug :app:assembleDebugAndroidTest --no-daemon --stacktrace"
        )
    },
    [ordered]@{
        id = "supply-chain"
        job = "Supply chain lock + SBOM"
        scope = "Backend transitive Python lock, npm lockfiles, and CycloneDX SBOM generation"
        commands = @(
            "npm run deps:verify",
            "npm run sbom:generate"
        )
    },
    [ordered]@{
        id = "extension-security"
        job = "IPC + Skill/MCP + settings security gate"
        scope = "IPC security policy/openExternal smoke, Skill Ed25519 signature/permission/upgrade-diff tests, Skill/MCP release-profile supply-chain controls, MCP schema/SSRF tests, and sensitive settings server-side enforcement"
        commands = @(
            "npm run security:extensions"
        )
    }
)

$needsResults = ConvertTo-NeedsResultMap $NeedsJson
$hasCiResults = $needsResults.Count -gt 0 -and -not $needsResults.ContainsKey("needs-json-parse-error")
$failedItems = New-Object System.Collections.Generic.List[string]

foreach ($gate in $gates) {
    $result = if ($needsResults.ContainsKey($gate.id)) { [string]$needsResults[$gate.id] } else { "not_reported" }
    $gate["result"] = $result
    if ($hasCiResults -and $result -ne "success") {
        $failedItems.Add("$($gate.job): $result")
    }
}
if ($needsResults.ContainsKey("needs-json-parse-error")) {
    $failedItems.Add("RELEASE_EVIDENCE_NEEDS_JSON could not be parsed.")
}
if (-not $hasCiResults -and -not $needsResults.ContainsKey("needs-json-parse-error")) {
    $failedItems.Add("CI job results were not supplied; this file was generated outside the CI summary job or without RELEASE_EVIDENCE_NEEDS_JSON.")
}

$ciStatus = if ($hasCiResults -and $failedItems.Count -eq 0) {
    "machine_gates_passed"
}
elseif ($hasCiResults) {
    "machine_gates_failed_or_incomplete"
}
else {
    "ci_results_unavailable"
}

$manualStatus = if (-not [string]::IsNullOrWhiteSpace($ManualSignoffStatus)) {
    $ManualSignoffStatus.Trim()
}
elseif (-not [string]::IsNullOrWhiteSpace($env:RELEASE_EVIDENCE_MANUAL_SIGNOFF_STATUS)) {
    ([string]$env:RELEASE_EVIDENCE_MANUAL_SIGNOFF_STATUS).Trim()
}
elseif ($OwnerSignature -eq "PENDING_RELEASE_OWNER_SIGNATURE") {
    "manual_signoff_pending"
}
else {
    "manual_signature_recorded_review_required"
}

$ownerSignatureVerificationStatus = if ($OwnerSignature -eq "PENDING_RELEASE_OWNER_SIGNATURE") {
    "pending"
}
else {
    "not_verified"
}
$ownerSignaturePayloadSha256 = "PENDING"
$ownerSignatureKeyFingerprint = "PENDING"
$ownerSignatureVerificationError = ""
$ownerSignatureVerificationRelativePath = "build/release-owner-signature-verification.json"
$ownerSignatureVerificationPath = Join-Path $resolvedRoot $ownerSignatureVerificationRelativePath
if ($StrictReleaseSignoff) {
    $verificationScript = Join-Path $PSScriptRoot "release_owner_signature.py"
    New-Item -ItemType Directory -Path (Split-Path -Parent $ownerSignatureVerificationPath) -Force | Out-Null
    & python $verificationScript --output $ownerSignatureVerificationPath
    if ($LASTEXITCODE -ne 0) {
        $ownerSignatureVerificationError = "Cryptographic release-owner signature verification failed."
    }
    elseif (-not (Test-Path -LiteralPath $ownerSignatureVerificationPath -PathType Leaf)) {
        $ownerSignatureVerificationError = "Cryptographic release-owner signature evidence was not produced."
    }
    else {
        try {
            $ownerSignatureVerification = Get-Content -LiteralPath $ownerSignatureVerificationPath -Raw | ConvertFrom-Json
            if ($ownerSignatureVerification.verified -ne $true) {
                throw "verification evidence did not record verified=true"
            }
            $ownerSignaturePayloadSha256 = ([string]$ownerSignatureVerification.payload_sha256).Trim()
            $ownerSignatureKeyFingerprint = ([string]$ownerSignatureVerification.public_key_fingerprint).Trim()
            if ($ownerSignaturePayloadSha256 -notmatch '^sha256:[0-9a-f]{64}$') {
                throw "payload digest is invalid"
            }
            if ($ownerSignatureKeyFingerprint -notmatch '^sha256:[0-9a-f]{64}$') {
                throw "public-key fingerprint is invalid"
            }
            $ownerSignatureVerificationStatus = "verified"
        }
        catch {
            $ownerSignatureVerificationError = "Cryptographic release-owner signature evidence is invalid."
        }
    }
}

$strictReleaseEvidenceErrors = New-Object System.Collections.Generic.List[string]
if ($StrictReleaseSignoff) {
    if ($ciStatus -ne "machine_gates_passed") {
        $strictReleaseEvidenceErrors.Add("CI status must be machine_gates_passed; got $ciStatus.")
    }
    if ($worktreeStatus -ne "clean") {
        $strictReleaseEvidenceErrors.Add("Worktree status must be clean; got $worktreeStatus.")
    }
    if ($OwnerSignature -eq "PENDING_RELEASE_OWNER_SIGNATURE" -or [string]::IsNullOrWhiteSpace($OwnerSignature)) {
        $strictReleaseEvidenceErrors.Add("Owner signature must be recorded for strict RC evidence.")
    }
    if ($ownerSignatureVerificationStatus -ne "verified") {
        $message = if ([string]::IsNullOrWhiteSpace($ownerSignatureVerificationError)) {
            "Owner signature must be cryptographically verified for strict RC evidence."
        }
        else {
            $ownerSignatureVerificationError
        }
        $strictReleaseEvidenceErrors.Add($message)
    }
    $acceptedManualStatuses = @(
        "rc_signoff_recorded",
        "release_signoff_recorded",
        "paid_launch_signoff_recorded"
    )
    if (-not ($acceptedManualStatuses -contains $manualStatus)) {
        $strictReleaseEvidenceErrors.Add(
            "Manual sign-off status must be one of $($acceptedManualStatuses -join ', '); got $manualStatus."
        )
    }
}

$machineEnvironment = [ordered]@{
    runner_os = Get-FirstNonEmpty @($env:RUNNER_OS, [System.Environment]::OSVersion.Platform.ToString()) "unknown"
    runner_arch = Get-FirstNonEmpty @($env:RUNNER_ARCH, [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()) "unknown"
    runner_name = Get-FirstNonEmpty @($env:RUNNER_NAME, [System.Environment]::MachineName) "unknown"
    image_os = Get-FirstNonEmpty @($env:ImageOS, "unknown") "unknown"
    image_version = Get-FirstNonEmpty @($env:ImageVersion, "unknown") "unknown"
    os_description = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
    powershell = $PSVersionTable.PSVersion.ToString()
    node = Get-ToolVersion "node" @("--version")
    npm = Get-ToolVersion "npm" @("--version")
    python = Get-ToolVersion "python" @("--version")
    git = Get-ToolVersion "git" @("--version")
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# Current Release Evidence")
$lines.Add("")
$lines.Add("> Generated by CI or by ``scripts/generate_current_release_evidence.ps1``. This is the single current release evidence summary. It is not release sign-off until the manual acceptance items, waivers/residual risks, and owner signature are complete.")
$lines.Add("")
$lines.Add("## Summary")
$lines.Add("")
$lines.Add("- Commit SHA: $CommitSha")
$lines.Add("- Release version: $releaseVersionLabel")
$lines.Add("- Build identifier: $BuildIdentifier")
$lines.Add("- Date (UTC): $GeneratedAtUtc")
$lines.Add("- CI status: $ciStatus")
$lines.Add("- Worktree status: $worktreeStatus")
$lines.Add("- Worktree dirty file count: $worktreeDirtyCount")
$lines.Add("- Manual sign-off status: $manualStatus")
$lines.Add("- Release owner: $ReleaseOwner")
$lines.Add("- Owner signature: $OwnerSignature")
$lines.Add("- Owner signature verification: $ownerSignatureVerificationStatus")
$lines.Add("- Owner signature payload SHA-256: $ownerSignaturePayloadSha256")
$lines.Add("- Owner signature key fingerprint: $ownerSignatureKeyFingerprint")
$lines.Add("- Owner signature evidence: $ownerSignatureVerificationRelativePath")
$lines.Add("- Workflow: $boundWorkflow")
$lines.Add("- Run id: $boundRunId")
$lines.Add("- Run attempt: $boundRunAttempt")
$lines.Add("")

$lines.Add("## Machine Environment")
$lines.Add("")
$lines.Add("| Field | Value |")
$lines.Add("| --- | --- |")
foreach ($entry in $machineEnvironment.GetEnumerator()) {
    $lines.Add("| $(Escape-MarkdownCell $entry.Key) | $(Escape-MarkdownCell ([string]$entry.Value)) |")
}
$lines.Add("")

$lines.Add("## Execution Commands")
$lines.Add("")
$lines.Add("| CI job | Command |")
$lines.Add("| --- | --- |")
foreach ($gate in $gates) {
    foreach ($command in $gate.commands) {
        $escapedCommand = Escape-MarkdownCell $command
        $lines.Add("| $(Escape-MarkdownCell $gate.job) | ``$escapedCommand`` |")
    }
}
$lines.Add("")

$lines.Add("## All Test Results")
$lines.Add("")
$lines.Add("| CI job | Scope | Result |")
$lines.Add("| --- | --- | --- |")
foreach ($gate in $gates) {
    $lines.Add("| $(Escape-MarkdownCell $gate.job) | $(Escape-MarkdownCell $gate.scope) | $(Escape-MarkdownCell $gate.result) |")
}
$lines.Add("")

Add-ListSection $lines "Failed Items" ([string[]]$failedItems.ToArray()) "None recorded from CI job results."
Add-ListSection $lines "Exemptions" ([string[]]$waivers.ToArray()) "None recorded. Any waiver must include owner, reason, expiry condition, and follow-up task before release."
Add-ListSection $lines "Manual Acceptance Items" ([string[]]$manualItems.ToArray()) "None recorded."
Add-ListSection $lines "Artifact Links" ([string[]]$artifactLinks.ToArray()) "None recorded."

$lines.Add("## Owner Signature")
$lines.Add("")
$lines.Add("- Owner: $ReleaseOwner")
$lines.Add("- Signature: $OwnerSignature")
$lines.Add("- Verification: $ownerSignatureVerificationStatus")
$lines.Add("- Payload SHA-256: $ownerSignaturePayloadSha256")
$lines.Add("- Public-key fingerprint: $ownerSignatureKeyFingerprint")
$lines.Add("- Verification evidence: $ownerSignatureVerificationRelativePath")
$lines.Add("- Signed at UTC: $(if ($OwnerSignature -eq 'PENDING_RELEASE_OWNER_SIGNATURE') { 'PENDING' } else { $GeneratedAtUtc })")
$lines.Add("")

$markdown = $lines -join "`n"
$outputDir = Split-Path -Parent $resolvedOutputPath
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
[System.IO.File]::WriteAllText($resolvedOutputPath, $markdown, (New-Object System.Text.UTF8Encoding $false))

Write-Host "Current release evidence generated: $resolvedOutputPath"

if ($StrictReleaseSignoff -and $strictReleaseEvidenceErrors.Count -gt 0) {
    [Console]::Error.WriteLine(
        "Strict current release evidence failed: $($strictReleaseEvidenceErrors -join ' ')"
    )
    exit 1
}
