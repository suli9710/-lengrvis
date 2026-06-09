[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$Candidate = "",
    [string]$EvidenceMode = "unknown",
    [string]$Platform = "",
    [string]$ArtifactUnderTest = "",
    [string]$BuildIdentifier = "",
    [string]$ProfileUnderTest = "",
    [string]$Runtime = "",
    [string]$RuntimeVersion = "",
    [string]$RuntimeSource = "",
    [string]$Model = "",
    [string]$ModelVersion = "",
    [string]$ModelSource = "",
    [string[]]$BlockedReason = @(),
    [string]$InstallOutcome = "",
    [string[]]$InstallBlockedReason = @(),
    [string]$StartOutcome = "",
    [string[]]$StartBlockedReason = @(),
    [string]$PullOutcome = "",
    [string[]]$PullBlockedReason = @(),
    [string]$TaskSmokeOutcome = "",
    [string[]]$TaskSmokeBlockedReason = @(),
    [string[]]$Artifact = @()
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

function Test-Configured([string]$Value) {
    return -not [string]::IsNullOrWhiteSpace($Value)
}

function Resolve-OutputPath([string]$PathValue, [string]$DefaultRelativePath) {
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
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)=[A-Za-z0-9._-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
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
    $text = [regex]::Replace($text, "(?i)(bearer\s+)[A-Za-z0-9._~-]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(token|api[_-]?key|secret|password|code)=([^&\s,;]+)", '${1}=[redacted]')
    $text = [regex]::Replace($text, "sk-[A-Za-z0-9._-]{8,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "[A-Za-z]:\\[^\s,;]+", "[redacted-path]")
    $text = [regex]::Replace($text, "(?<!\w)/(?:Users|home)/[^\s,;]+", "[redacted-path]")
    return (Redact-DisplayLabel $text)
}

function ConvertTo-RedactedList([string[]]$Values) {
    $items = New-Object System.Collections.Generic.List[string]
    foreach ($value in $Values) {
        $redacted = Redact-TextValue $value
        if (Test-Configured $redacted) {
            $items.Add($redacted)
        }
    }
    return @($items)
}

function Get-ArtifactBuildProfileStatus([string[]]$Values) {
    foreach ($value in $Values) {
        if (-not (Test-Configured $value)) {
            return "missing_required_fields"
        }
    }
    return "recorded_unverified_by_this_helper"
}

function Test-StepOutcomeOrBlockedReasonConfigured([string]$Outcome, [string[]]$StepBlockedReason, [string[]]$FallbackBlockedReasons) {
    $redactedOutcome = Redact-TextValue $Outcome
    $redactedStepReasons = ConvertTo-RedactedList $StepBlockedReason
    return (Test-Configured $redactedOutcome) -or ($redactedStepReasons.Count -gt 0) -or (@($FallbackBlockedReasons).Count -gt 0)
}

function New-ManualStepOutcome([string]$StepName, [string]$Outcome, [string[]]$StepBlockedReason, [string[]]$FallbackBlockedReasons) {
    $redactedOutcome = Redact-TextValue $Outcome
    $redactedReasons = ConvertTo-RedactedList $StepBlockedReason
    $usedOverallReason = $false

    if ((-not (Test-Configured $redactedOutcome)) -and $redactedReasons.Count -eq 0 -and @($FallbackBlockedReasons).Count -gt 0) {
        $redactedReasons = @($FallbackBlockedReasons)
        $usedOverallReason = $true
    }

    if (Test-Configured $redactedOutcome) {
        $safeOutcome = $redactedOutcome
        $stepStatus = "manual_outcome_recorded_unverified_by_this_helper"
    }
    elseif ($redactedReasons.Count -gt 0) {
        $safeOutcome = "blocked"
        $stepStatus = if ($usedOverallReason) { "blocked_by_overall_reason_recorded" } else { "blocked_reason_recorded" }
    }
    else {
        $safeOutcome = "uncollected"
        $stepStatus = "blocked_missing_outcome_or_blocked_reason"
    }

    return [ordered]@{
        step = $StepName
        outcome = $safeOutcome
        status = $stepStatus
        blocked_reason_redacted = @($redactedReasons)
        pass_verified_by_this_helper = $false
        clean_machine_pass = $false
    }
}

function New-MissingFieldHint([string]$FieldName) {
    $missingArtifact = "manual clean-machine evidence field"
    $howToCollect = "Collect the observation on a clean machine or clean packaged profile, then rerun this helper with the matching parameter."
    $helperArgument = "<matching helper parameter>"

    switch ($FieldName) {
        "artifact_build_profile.artifact_under_test" {
            $missingArtifact = "candidate artifact under test"
            $howToCollect = "Record the installer, portable bundle, or packaged artifact label used for the clean-machine run."
            $helperArgument = "-ArtifactUnderTest <candidate artifact path or release label>"
            break
        }
        "artifact_build_profile.build_identifier" {
            $missingArtifact = "candidate build identifier"
            $howToCollect = "Record the candidate commit, build number, package hash label, or release-candidate id."
            $helperArgument = "-BuildIdentifier <commit, build id, package hash label, or RC id>"
            break
        }
        "artifact_build_profile.profile_under_test" {
            $missingArtifact = "clean machine or clean packaged profile label"
            $howToCollect = "Record the disposable machine/profile label used for the manual run, not a private full path."
            $helperArgument = "-ProfileUnderTest <clean machine/profile label>"
            break
        }
        "runtime.name" {
            $missingArtifact = "local runtime name"
            $howToCollect = "Record the runtime observed on the clean machine, for example Ollama or the bundled runtime name."
            $helperArgument = "-Runtime <runtime name>"
            break
        }
        "runtime.version" {
            $missingArtifact = "local runtime version"
            $howToCollect = "Record the runtime version visible in the product, runtime CLI, or reviewed screenshot/log label."
            $helperArgument = "-RuntimeVersion <runtime version>"
            break
        }
        "model.name" {
            $missingArtifact = "local model name"
            $howToCollect = "Record the recommended or pulled model name visible during the clean-machine run."
            $helperArgument = "-Model <model name>"
            break
        }
        "model.version" {
            $missingArtifact = "local model version or digest"
            $howToCollect = "Record the model version, tag, or digest visible during the clean-machine run."
            $helperArgument = "-ModelVersion <model tag, version, or digest>"
            break
        }
        "clean_machine_run.install.outcome_or_blocked_reason" {
            $missingArtifact = "manual clean-machine install outcome or install blocked reason"
            $howToCollect = "On the clean machine/profile, run the candidate install or setup path until the install step is observed; record a redacted outcome or why it was unavailable."
            $helperArgument = "-InstallOutcome <observed install outcome> or -InstallBlockedReason <why install could not be observed>"
            break
        }
        "clean_machine_run.start.outcome_or_blocked_reason" {
            $missingArtifact = "manual clean-machine runtime start outcome or start blocked reason"
            $howToCollect = "Start the local runtime through the product or documented manual path; record the visible outcome or why it was unavailable."
            $helperArgument = "-StartOutcome <observed start outcome> or -StartBlockedReason <why start could not be observed>"
            break
        }
        "clean_machine_run.pull.outcome_or_blocked_reason" {
            $missingArtifact = "manual clean-machine model pull/availability outcome or pull blocked reason"
            $howToCollect = "Pull or verify availability of the target model on the clean machine; record the visible outcome or why it was unavailable."
            $helperArgument = "-PullOutcome <observed pull/availability outcome> or -PullBlockedReason <why pull could not be observed>"
            break
        }
        "clean_machine_run.task_smoke.outcome_or_blocked_reason" {
            $missingArtifact = "manual clean-machine local-model task-smoke outcome or task-smoke blocked reason"
            $howToCollect = "Run one beginner-visible local-model task on the clean machine; record the result-quality observation or why the smoke could not run."
            $helperArgument = "-TaskSmokeOutcome <observed task-smoke outcome> or -TaskSmokeBlockedReason <why task smoke could not be observed>"
            break
        }
    }

    return [ordered]@{
        field = $FieldName
        missing_artifact = $missingArtifact
        how_to_collect = $howToCollect
        helper_argument = $helperArgument
    }
}

$evidenceRootPath = Resolve-OutputPath $EvidenceRoot ".tmp\local-model-clean-machine-evidence"
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $evidenceRootPath "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$jsonPath = Join-Path $runRoot "local-model-clean-machine-evidence.redacted.json"
$markdownPath = Join-Path $runRoot "local-model-clean-machine-evidence.redacted.md"

$missingFields = New-Object System.Collections.Generic.List[string]
if (-not (Test-Configured $ArtifactUnderTest)) { $missingFields.Add("artifact_build_profile.artifact_under_test") }
if (-not (Test-Configured $BuildIdentifier)) { $missingFields.Add("artifact_build_profile.build_identifier") }
if (-not (Test-Configured $ProfileUnderTest)) { $missingFields.Add("artifact_build_profile.profile_under_test") }
if (-not (Test-Configured $Runtime)) { $missingFields.Add("runtime.name") }
if (-not (Test-Configured $RuntimeVersion)) { $missingFields.Add("runtime.version") }
if (-not (Test-Configured $Model)) { $missingFields.Add("model.name") }
if (-not (Test-Configured $ModelVersion)) { $missingFields.Add("model.version") }

$redactedBlockedReasons = ConvertTo-RedactedList $BlockedReason
$redactedArtifacts = ConvertTo-RedactedList $Artifact
$hasManualBlockedReason = $redactedBlockedReasons.Count -gt 0

if (-not (Test-StepOutcomeOrBlockedReasonConfigured -Outcome $InstallOutcome -StepBlockedReason $InstallBlockedReason -FallbackBlockedReasons $redactedBlockedReasons)) {
    $missingFields.Add("clean_machine_run.install.outcome_or_blocked_reason")
}
if (-not (Test-StepOutcomeOrBlockedReasonConfigured -Outcome $StartOutcome -StepBlockedReason $StartBlockedReason -FallbackBlockedReasons $redactedBlockedReasons)) {
    $missingFields.Add("clean_machine_run.start.outcome_or_blocked_reason")
}
if (-not (Test-StepOutcomeOrBlockedReasonConfigured -Outcome $PullOutcome -StepBlockedReason $PullBlockedReason -FallbackBlockedReasons $redactedBlockedReasons)) {
    $missingFields.Add("clean_machine_run.pull.outcome_or_blocked_reason")
}
if (-not (Test-StepOutcomeOrBlockedReasonConfigured -Outcome $TaskSmokeOutcome -StepBlockedReason $TaskSmokeBlockedReason -FallbackBlockedReasons $redactedBlockedReasons)) {
    $missingFields.Add("clean_machine_run.task_smoke.outcome_or_blocked_reason")
}

$allRequiredFieldsPresent = $missingFields.Count -eq 0
$missingFieldHints = @()
foreach ($field in $missingFields) {
    $missingFieldHints += ,(New-MissingFieldHint ([string]$field))
}

if ((-not $allRequiredFieldsPresent) -and (-not $hasManualBlockedReason)) {
    $redactedBlockedReasons = @(
        "missing clean-machine local-model evidence fields; record artifact/build/profile, runtime/model/version, and install/start/pull/task-smoke outcome or exact blocked reason before claiming local/offline model readiness"
    )
}

$redactedArtifactUnderTest = (Redact-TextValue $ArtifactUnderTest)
$redactedBuildIdentifier = (Redact-TextValue $BuildIdentifier)
$redactedProfileUnderTest = (Redact-TextValue $ProfileUnderTest)
$artifactBuildProfileStatus = Get-ArtifactBuildProfileStatus @(
    $redactedArtifactUnderTest,
    $redactedBuildIdentifier,
    $redactedProfileUnderTest
)
$cleanMachineRun = [ordered]@{
    install = New-ManualStepOutcome -StepName "install" -Outcome $InstallOutcome -StepBlockedReason $InstallBlockedReason -FallbackBlockedReasons $redactedBlockedReasons
    start = New-ManualStepOutcome -StepName "start" -Outcome $StartOutcome -StepBlockedReason $StartBlockedReason -FallbackBlockedReasons $redactedBlockedReasons
    pull = New-ManualStepOutcome -StepName "pull" -Outcome $PullOutcome -StepBlockedReason $PullBlockedReason -FallbackBlockedReasons $redactedBlockedReasons
    task_smoke = New-ManualStepOutcome -StepName "task_smoke" -Outcome $TaskSmokeOutcome -StepBlockedReason $TaskSmokeBlockedReason -FallbackBlockedReasons $redactedBlockedReasons
}
$hasAnyBlockedReason = $redactedBlockedReasons.Count -gt 0
foreach ($stepName in @("install", "start", "pull", "task_smoke")) {
    if ($cleanMachineRun[$stepName].blocked_reason_redacted.Count -gt 0) {
        $hasAnyBlockedReason = $true
    }
}
$hasManualStepBlockedReason = (
    (ConvertTo-RedactedList $InstallBlockedReason).Count -gt 0 -or
    (ConvertTo-RedactedList $StartBlockedReason).Count -gt 0 -or
    (ConvertTo-RedactedList $PullBlockedReason).Count -gt 0 -or
    (ConvertTo-RedactedList $TaskSmokeBlockedReason).Count -gt 0
)
$hasManualBlockedEvidence = $hasManualBlockedReason -or $hasManualStepBlockedReason
$missingEvidenceArtifacts = @()
$missingEvidenceArtifactStatus = "redacted_artifact_labels_recorded_unverified_by_this_helper"
if ($redactedArtifacts.Count -eq 0) {
    $missingEvidenceArtifactStatus = "missing_redacted_artifact_labels"
    $missingEvidenceArtifacts = @(
        "redacted screenshot/log labels for the manual clean-machine install/start/pull/task-smoke run; add with -Artifact <reviewed screenshot or log label>"
    )
}
$nextHelperCommandTemplate = ".\scripts\collect_local_model_clean_machine_evidence_template.ps1 -EvidenceMode clean-machine -Platform <platform> -ArtifactUnderTest <candidate artifact> -BuildIdentifier <build id> -ProfileUnderTest <clean profile label> -Runtime <runtime name> -RuntimeVersion <runtime version> -Model <model name> -ModelVersion <model version> -InstallOutcome <observed install outcome> -StartOutcome <observed start outcome> -PullOutcome <observed pull/availability outcome> -TaskSmokeOutcome <observed task-smoke outcome> -Artifact <reviewed screenshot or log label>"
$blockedRunHelperCommandTemplate = ".\scripts\collect_local_model_clean_machine_evidence_template.ps1 -EvidenceMode clean-machine -ArtifactUnderTest <candidate artifact> -BuildIdentifier <build id> -ProfileUnderTest <clean profile label> -Runtime <runtime name if known> -RuntimeVersion <runtime version if known> -Model <model name if known> -ModelVersion <model version if known> -BlockedReason <why the clean-machine run is blocked>"

$templateStatus = if ($allRequiredFieldsPresent -and (-not $hasManualBlockedEvidence) -and (-not $hasAnyBlockedReason)) {
    "manual_review_ready"
}
elseif ($hasManualBlockedEvidence) {
    "blocked_reason_recorded"
}
else {
    "blocked_missing_required_fields"
}

$packet = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    generated_by = "scripts/collect_local_model_clean_machine_evidence_template.ps1"
    marker = "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
    outputs = [ordered]@{
        redacted_json = (Get-DisplayPath $jsonPath)
        redacted_markdown = (Get-DisplayPath $markdownPath)
    }
    readonly_scope = [ordered]@{
        starts_product_processes = $false
        performs_network_requests = $false
        installs_runtime = $false
        starts_runtime = $false
        pulls_models = $false
        runs_model_inference = $false
        changes_backend_product_logic = $false
        changes_desktop_ui = $false
        writes_only_evidence_template_artifacts = $true
    }
    redaction = [ordered]@{
        path_policy = "workspace-relative paths or file labels only"
        raw_logs_included = $false
        secrets_or_tokens_read = $false
        urls_redacted = $true
    }
    summary = [ordered]@{
        template_status = $templateStatus
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        real_install_start_pull_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        release_candidate_signoff = $false
        artifact_build_profile_status = $artifactBuildProfileStatus
        required_run_step_outcomes_recorded = ($missingFields | Where-Object { ([string]$_).StartsWith("clean_machine_run.", [System.StringComparison]::Ordinal) }).Count -eq 0
        missing_required_fields_count = $missingFields.Count
        missing_required_fields = @($missingFields)
    }
    evidence_template = [ordered]@{
        template_status = "manual_clean_machine_local_model_evidence_required"
        candidate = (Redact-TextValue $Candidate)
        evidence_mode = (Redact-TextValue $EvidenceMode)
        platform = (Redact-TextValue $Platform)
        artifact_build_profile = [ordered]@{
            status = $artifactBuildProfileStatus
            artifact = [ordered]@{
                label = $redactedArtifactUnderTest
                status = "unverified_by_this_helper"
            }
            build = [ordered]@{
                identifier = $redactedBuildIdentifier
                status = "unverified_by_this_helper"
            }
            profile = [ordered]@{
                label = $redactedProfileUnderTest
                status = "unverified_by_this_helper"
            }
        }
        runtime = [ordered]@{
            name = (Redact-TextValue $Runtime)
            version = (Redact-TextValue $RuntimeVersion)
            source = (Redact-TextValue $RuntimeSource)
            status = "unverified_by_this_helper"
        }
        model = [ordered]@{
            name = (Redact-TextValue $Model)
            version = (Redact-TextValue $ModelVersion)
            source = (Redact-TextValue $ModelSource)
            status = "unverified_by_this_helper"
        }
        clean_machine_run = $cleanMachineRun
        blocked_reason_redacted = @($redactedBlockedReasons)
        observed_artifacts_redacted = @($redactedArtifacts)
        actionable_handoff = [ordered]@{
            status = $templateStatus
            missing_now = @($missingFieldHints)
            missing_evidence_artifacts_status = $missingEvidenceArtifactStatus
            missing_evidence_artifacts = @($missingEvidenceArtifacts)
            next_helper_command_template = $nextHelperCommandTemplate
            blocked_run_helper_command_template = $blockedRunHelperCommandTemplate
            next_manual_run = "Run the candidate on a clean machine or clean packaged profile outside this helper, capture reviewed/redacted observations, then rerun this helper with the missing parameters above."
            not_a_pass = "This remains a handoff template only; it must not be recorded as clean-machine local-model readiness or release-candidate sign-off."
            pass_defaults_remain_false = $true
        }
        required_fields = @(
            "artifact_build_profile.artifact_under_test",
            "artifact_build_profile.build_identifier",
            "artifact_build_profile.profile_under_test",
            "runtime.name",
            "runtime.version",
            "model.name",
            "model.version",
            "clean_machine_run.install.outcome_or_blocked_reason",
            "clean_machine_run.start.outcome_or_blocked_reason",
            "clean_machine_run.pull.outcome_or_blocked_reason",
            "clean_machine_run.task_smoke.outcome_or_blocked_reason",
            "blocked_reason_redacted when any runtime/model/version evidence is unavailable"
        )
        required_redactions = @(
            "user names and organization folders in paths",
            "tokens, API keys, cookies, pairing codes, and one-time codes",
            "private model cache paths",
            "raw console logs unless separately reviewed",
            "non-local hostnames or IP addresses"
        )
        must_not_be_recorded_as = @(
            "true local model install pass",
            "true local model start pass",
            "true local model pull pass",
            "true local model task-smoke pass",
            "clean-machine local-model readiness",
            "template/dev smoke clean-machine pass",
            "release-candidate sign-off"
        )
        next_manual_evidence_needed = @(
            "Run the candidate on a clean machine or clean packaged profile.",
            "Record the exact artifact under test, build identifier, and clean profile label.",
            "Record the runtime name/version and whether the runtime was already present, installed manually, or blocked.",
            "Record the model name/version or exact blocked reason.",
            "Record install/start/pull/task-smoke outcome for the clean-machine run, or a blocked reason for each unavailable step.",
            "Attach redacted screenshots/log labels that prove the observed user-visible state.",
            "Keep this helper output separate from the actual install/start/pull evidence."
        )
    }
}

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# Local Model Clean-Machine Evidence Template")
$markdownLines.Add("")
$markdownLines.Add("- Marker: $($packet.marker)")
$markdownLines.Add("- Generated: $($packet.generated_at_utc)")
$markdownLines.Add("- JSON: $($packet.outputs.redacted_json)")
$markdownLines.Add("- Markdown: $($packet.outputs.redacted_markdown)")
$markdownLines.Add("- Status: $($packet.summary.template_status)")
$markdownLines.Add("- Scope: read-only helper; no product process starts, no network requests, no runtime install, no runtime start, no model pull.")
$markdownLines.Add("")
$markdownLines.Add("## Missing Now")
if ($packet.evidence_template.actionable_handoff.missing_now.Count -eq 0) {
    $markdownLines.Add("- none for this template; actual clean-machine evidence still needs manual review outside this helper before any pass/sign-off claim")
}
else {
    foreach ($hint in $packet.evidence_template.actionable_handoff.missing_now) {
        $markdownLines.Add("- $($hint.field): missing $($hint.missing_artifact); rerun with $($hint.helper_argument)")
    }
}
foreach ($artifact in $packet.evidence_template.actionable_handoff.missing_evidence_artifacts) {
    $markdownLines.Add("- evidence artifact label: $artifact")
}
$markdownLines.Add("")
$markdownLines.Add("## Next Helper Command Template")
$markdownLines.Add('```powershell')
$markdownLines.Add($packet.evidence_template.actionable_handoff.next_helper_command_template)
$markdownLines.Add('```')
$markdownLines.Add("")
$markdownLines.Add("## Blocked Run Helper Command Template")
$markdownLines.Add('```powershell')
$markdownLines.Add($packet.evidence_template.actionable_handoff.blocked_run_helper_command_template)
$markdownLines.Add('```')
$markdownLines.Add("")
$markdownLines.Add("## Required Fields")
foreach ($field in $packet.evidence_template.required_fields) {
    $markdownLines.Add("- $field")
}
$markdownLines.Add("")
$markdownLines.Add("## Current Entry")
$markdownLines.Add("- Candidate: $($packet.evidence_template.candidate)")
$markdownLines.Add("- Evidence mode: $($packet.evidence_template.evidence_mode)")
$markdownLines.Add("- Platform: $($packet.evidence_template.platform)")
$markdownLines.Add("- Artifact under test: $($packet.evidence_template.artifact_build_profile.artifact.label)")
$markdownLines.Add("- Build identifier: $($packet.evidence_template.artifact_build_profile.build.identifier)")
$markdownLines.Add("- Profile under test: $($packet.evidence_template.artifact_build_profile.profile.label)")
$markdownLines.Add("- Runtime: $($packet.evidence_template.runtime.name) $($packet.evidence_template.runtime.version)")
$markdownLines.Add("- Model: $($packet.evidence_template.model.name) $($packet.evidence_template.model.version)")
$markdownLines.Add("")
$markdownLines.Add("## Clean-Machine Run Outcomes")
foreach ($stepName in @("install", "start", "pull", "task_smoke")) {
    $step = $packet.evidence_template.clean_machine_run[$stepName]
    $markdownLines.Add("- ${stepName}: outcome=$($step.outcome); status=$($step.status); clean_machine_pass=$($step.clean_machine_pass)")
    foreach ($reason in $step.blocked_reason_redacted) {
        $markdownLines.Add("  - blocked_reason: $reason")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Blocked Reason Redacted")
foreach ($reason in $packet.evidence_template.blocked_reason_redacted) {
    $markdownLines.Add("- $reason")
}
$markdownLines.Add("")
$markdownLines.Add("## Observed Artifacts Redacted")
if ($packet.evidence_template.observed_artifacts_redacted.Count -eq 0) {
    $markdownLines.Add("- none recorded")
}
else {
    foreach ($artifact in $packet.evidence_template.observed_artifacts_redacted) {
        $markdownLines.Add("- $artifact")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Not Sign-Off")
foreach ($item in $packet.evidence_template.must_not_be_recorded_as) {
    $markdownLines.Add("- $item")
}
$markdown = $markdownLines -join "`n"

$packet | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $jsonPath -Encoding utf8
$markdown | Set-Content -LiteralPath $markdownPath -Encoding utf8

Write-Host "Local model clean-machine evidence template"
Write-Host "Marker: $($packet.marker)"
Write-Host "Redacted JSON: $($packet.outputs.redacted_json)"
Write-Host "Redacted Markdown: $($packet.outputs.redacted_markdown)"
Write-Host ""
Write-Host "What is missing now:"
if ($packet.evidence_template.actionable_handoff.missing_now.Count -eq 0) {
    Write-Host "- no required template fields are missing; actual clean-machine evidence still needs manual review outside this helper."
}
else {
    foreach ($hint in $packet.evidence_template.actionable_handoff.missing_now) {
        Write-Host "- $($hint.field): rerun with $($hint.helper_argument)"
    }
}
foreach ($artifact in $packet.evidence_template.actionable_handoff.missing_evidence_artifacts) {
    Write-Host "- evidence artifact label: $artifact"
}
Write-Host ""
Write-Host "Next helper command template:"
Write-Host $packet.evidence_template.actionable_handoff.next_helper_command_template
Write-Host ""
Write-Host "This remains NOT a clean-machine pass; all pass/signoff fields stay false."
Write-Host ""
Write-Host $markdown

exit 0
