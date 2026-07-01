function New-SettingsLocalModelEvidenceSummary {
    param(
        [Parameter(Mandatory = $true)]$qaEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

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

    return [ordered]@{
        settingsContract = $settingsContract
        settingsArtifactNames = $settingsArtifactNames
        settingsArtifacts = $settingsArtifacts
        settingsArtifactsPresent = $settingsArtifactsPresent
    }
}

