function Redact-DisplayLabel([string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Label)) {
        return ""
    }

    $text = $Label.Trim()
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{4,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "(?i)\b(?:contoso|acme|customer)[A-Za-z0-9._-]*", "[redacted-org]")
    $text = [regex]::Replace($text, "(?i)([?&](?:token|api[_-]?key|client_secret|secret|password|code)=)[^&\s]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)([?&](?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=)[^&\s]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)\bhttps?://[^/\s\\]+", "https://[redacted-host]")
    $text = [regex]::Replace($text, "(?i)\bwss?://[^/\s\\]+", "wss://[redacted-host]")
    $text = [regex]::Replace($text, "\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-host]")
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)=[A-Za-z0-9._~+/=-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=[A-Za-z0-9._~+/=-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
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
    $text = [regex]::Replace($text, "(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)\b(set-cookie|cookie)\s*[:=]\s*[^,\r\n]+", '${1}: [redacted]')
    $text = [regex]::Replace($text, "(?i)\b(session|cookie|token|api[_-]?key|client_secret|secret|password|code|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=([^&\s,;]+)", '${1}=[redacted]')
    $text = [regex]::Replace($text, "(?i)\b(pairing\s+code|one[-\s]?time\s+(?:code|passcode|password)|otp)\s*[:=]?\s+[A-Za-z0-9._-]{4,}", '${1} [redacted]')
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{8,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "[A-Za-z]:\\[^\s,;]+", "[redacted-path]")
    $text = [regex]::Replace($text, "(?<!\w)/(?:Users|home)/[^\s,;]+", "[redacted-path]")
    return (Redact-DisplayLabel $text)
}
