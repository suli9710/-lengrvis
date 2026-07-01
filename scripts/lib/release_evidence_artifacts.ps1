function Find-LatestJsonArtifact([string]$RootPath, [string]$FileName) {
    if (-not (Test-Path -LiteralPath $RootPath)) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            data = $null
            error = ""
        }
    }

    $files = @(
        Get-ChildItem -LiteralPath $RootPath -Recurse -Filter $FileName -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($files.Count -eq 0) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            data = $null
            error = ""
        }
    }

    $latest = $files[0]
    try {
        $data = Get-Content -LiteralPath $latest.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            data = $data
            error = ""
        }
    }
    catch {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            data = $null
            error = "latest JSON artifact could not be parsed"
        }
    }
}

function Find-LatestTextArtifact([string]$RootPath, [string]$FileName) {
    if (-not (Test-Path -LiteralPath $RootPath)) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            text = ""
            error = ""
        }
    }

    $files = @(
        Get-ChildItem -LiteralPath $RootPath -Recurse -Filter $FileName -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($files.Count -eq 0) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            text = ""
            error = ""
        }
    }

    $latest = $files[0]
    try {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            text = Get-Content -LiteralPath $latest.FullName -Raw -Encoding UTF8
            error = ""
        }
    }
    catch {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            text = ""
            error = "latest text artifact could not be read"
        }
    }
}
