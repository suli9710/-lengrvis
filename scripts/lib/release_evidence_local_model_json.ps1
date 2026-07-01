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
