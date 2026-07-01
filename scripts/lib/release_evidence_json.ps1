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
