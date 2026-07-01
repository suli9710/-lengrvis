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
