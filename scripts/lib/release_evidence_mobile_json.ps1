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
