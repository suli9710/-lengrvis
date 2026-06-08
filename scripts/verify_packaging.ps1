param(
    [string]$DistDir = "dist",
    [string]$PortableDir = "dist\Lengrvis-win-portable",
    [string]$PortableZip = "dist\Lengrvis-win-portable.zip",
    [string]$SelfExtractingExe = "dist\Lengrvis-0.1.0-x64-self-extracting.exe",
    [switch]$RequireBundledOllama,
    [switch]$RunExecutableSmoke,
    [ValidateRange(1, 300)]
    [int]$SmokeTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Add-Type -AssemblyName System.IO.Compression.FileSystem

$Failures = New-Object System.Collections.Generic.List[string]
$MissingArtifacts = New-Object System.Collections.Generic.List[object]
$MinimumSelfExtractingExeBytes = 65536
$MinimumPortableLauncherBytes = 4096
$MinimumBackendExecutableBytes = 4096
$SmokeRunGuid = ([guid]::NewGuid().ToString("N")).Substring(0, 8)
$SmokeRunId = "run-{0}-{1}-{2}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $PID, $SmokeRunGuid
$SmokeLogRoot = Join-Path (Join-Path $Root ".tmp\packaging-smoke") $SmokeRunId
$script:SmokeRunIndex = 0

function Resolve-ProjectPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Test-RequiredFile([string]$Label, [string]$Path) {
    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        $Failures.Add("$Label missing: $FullPath")
        $MissingArtifacts.Add([pscustomobject]@{ Label = $Label; Path = $FullPath; Type = "file" })
        return
    }
    $Item = Get-Item -LiteralPath $FullPath
    if ($Item.Length -le 0) {
        $Failures.Add("$Label is empty: $FullPath")
        return
    }
    $Version = $Item.VersionInfo.FileVersion
    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = "n/a"
    }
    Write-Host "[ok] $Label ($($Item.Length) bytes, version $Version): $FullPath"
}

function Test-RequiredDirectory([string]$Label, [string]$Path) {
    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Container)) {
        $Failures.Add("$Label missing: $FullPath")
        $MissingArtifacts.Add([pscustomobject]@{ Label = $Label; Path = $FullPath; Type = "directory" })
        return
    }
    Write-Host "[ok] $Label`: $FullPath"
}

function Test-NonEmptyDirectory([string]$Label, [string]$Path) {
    Test-RequiredDirectory $Label $Path
    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Container)) {
        return
    }
    $FirstFile = Get-ChildItem -LiteralPath $FullPath -File -Recurse -Force -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $FirstFile) {
        $Failures.Add("$Label has no files: $FullPath")
        return
    }
    Write-Host "[ok] $Label contains files"
}

function Test-ZipEntry([System.IO.Compression.ZipArchive]$Zip, [string]$EntryName) {
    $Normalized = $EntryName -replace "\\", "/"
    $Entry = $Zip.Entries | Where-Object { ($_.FullName -replace "\\", "/") -eq $Normalized } | Select-Object -First 1
    if ($null -eq $Entry) {
        $Failures.Add("zip entry missing: $Normalized")
        return
    }
    if ($Entry.Length -le 0) {
        $Failures.Add("zip entry is empty: $Normalized")
        return
    }
    Write-Host "[ok] zip entry $Normalized ($($Entry.Length) bytes)"
}

function Test-ZipDirectoryEntry([System.IO.Compression.ZipArchive]$Zip, [string]$Prefix) {
    $Normalized = ($Prefix -replace "\\", "/").TrimEnd("/") + "/"
    $Entry = $Zip.Entries | Where-Object {
        ($_.FullName -replace "\\", "/").StartsWith($Normalized, [System.StringComparison]::OrdinalIgnoreCase) -and
        $_.Length -gt 0
    } | Select-Object -First 1
    if ($null -eq $Entry) {
        $Failures.Add("zip directory missing or empty: $Normalized")
        return
    }
    Write-Host "[ok] zip directory $Normalized contains files"
}

function Add-ReleaseSourceMapPolicyResult {
    param(
        [string]$Label,
        [System.Collections.Generic.List[string]]$Findings
    )

    if ($Findings.Count -eq 0) {
        Write-Host "[ok] $Label has no release source maps"
        return
    }

    $Preview = @($Findings | Select-Object -First 20)
    $More = if ($Findings.Count -gt 20) { "; ... $($Findings.Count - 20) more" } else { "" }
    $Failures.Add("$Label contains release source map artifacts: $($Preview -join '; ')$More")
}

function Test-ReleaseSourceMapFreeDirectory {
    param(
        [string]$Label,
        [string]$Path
    )

    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Container)) {
        return
    }

    $Findings = New-Object System.Collections.Generic.List[string]
    $Files = @(Get-ChildItem -LiteralPath $FullPath -File -Recurse -Force -ErrorAction SilentlyContinue)
    foreach ($File in $Files) {
        $Relative = $File.FullName
        try {
            $Relative = $File.FullName.Substring($Root.Path.Length).TrimStart('\', '/')
        }
        catch {
        }

        if ($File.Extension.Equals(".map", [System.StringComparison]::OrdinalIgnoreCase)) {
            $Findings.Add("$Relative is a source map file")
            continue
        }

        if ($File.Extension -in @(".js", ".css")) {
            $HasSourceMappingUrl = Select-String -LiteralPath $File.FullName -SimpleMatch "sourceMappingURL=" -Quiet -ErrorAction SilentlyContinue
            if ($HasSourceMappingUrl) {
                $Findings.Add("$Relative contains sourceMappingURL")
            }
        }
    }

    Add-ReleaseSourceMapPolicyResult -Label $Label -Findings $Findings
}

function Test-ZipReleaseSourceMapFree {
    param(
        [System.IO.Compression.ZipArchive]$Zip,
        [string]$Label,
        [string]$Prefix
    )

    $NormalizedPrefix = ($Prefix -replace "\\", "/").TrimEnd("/") + "/"
    $Findings = New-Object System.Collections.Generic.List[string]
    foreach ($Entry in $Zip.Entries) {
        $EntryName = $Entry.FullName -replace "\\", "/"
        if (-not $EntryName.StartsWith($NormalizedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        if ($Entry.Length -le 0) {
            continue
        }

        if ($EntryName.EndsWith(".map", [System.StringComparison]::OrdinalIgnoreCase)) {
            $Findings.Add("$EntryName is a source map file")
            continue
        }

        if ($EntryName.EndsWith(".js", [System.StringComparison]::OrdinalIgnoreCase) -or
            $EntryName.EndsWith(".css", [System.StringComparison]::OrdinalIgnoreCase)) {
            $Stream = $Entry.Open()
            $Reader = New-Object System.IO.StreamReader($Stream, [System.Text.Encoding]::UTF8, $true)
            try {
                $Text = $Reader.ReadToEnd()
                if ($Text.Contains("sourceMappingURL=")) {
                    $Findings.Add("$EntryName contains sourceMappingURL")
                }
            }
            finally {
                $Reader.Dispose()
            }
        }
    }

    Add-ReleaseSourceMapPolicyResult -Label $Label -Findings $Findings
}

function Test-PEExecutableHeader {
    param(
        [string]$Label,
        [string]$Path,
        [int64]$MinimumBytes
    )

    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        return $false
    }

    $Item = Get-Item -LiteralPath $FullPath
    if ($Item.Length -lt $MinimumBytes) {
        $Failures.Add("$Label is too small to be a runnable Windows executable ($($Item.Length) bytes; expected at least $MinimumBytes): $FullPath")
        return $false
    }

    $Stream = [System.IO.File]::Open($FullPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $Reader = New-Object System.IO.BinaryReader($Stream)
    try {
        if ($Stream.Length -lt 0x100) {
            $Failures.Add("$Label is too small for a PE header: $FullPath")
            return $false
        }

        $Mz = $Reader.ReadUInt16()
        if ($Mz -ne 0x5A4D) {
            $Failures.Add("$Label does not start with MZ header: $FullPath")
            return $false
        }

        $Stream.Position = 0x3C
        $PeOffset = [int64]$Reader.ReadUInt32()
        if ($PeOffset -lt 0x40 -or $PeOffset -gt ($Stream.Length - 0x18)) {
            $Failures.Add("$Label has invalid PE header offset $PeOffset`: $FullPath")
            return $false
        }

        $Stream.Position = $PeOffset
        $PeSignature = $Reader.ReadUInt32()
        if ($PeSignature -ne 0x00004550) {
            $Failures.Add("$Label has invalid PE signature at offset $PeOffset`: $FullPath")
            return $false
        }

        $Machine = $Reader.ReadUInt16()
        if ($Machine -notin @(0x014C, 0x8664, 0xAA64)) {
            $Failures.Add(("$Label uses unexpected PE machine 0x{0:X4}: {1}" -f $Machine, $FullPath))
        }

        $SectionCount = $Reader.ReadUInt16()
        if ($SectionCount -lt 1 -or $SectionCount -gt 96) {
            $Failures.Add("$Label has invalid PE section count $SectionCount`: $FullPath")
        }

        $Stream.Position = $PeOffset + 0x14
        $OptionalHeaderSize = $Reader.ReadUInt16()
        if ($OptionalHeaderSize -lt 0x60) {
            $Failures.Add("$Label optional header is too small ($OptionalHeaderSize bytes): $FullPath")
            return $false
        }

        $OptionalHeaderOffset = $PeOffset + 0x18
        if (($OptionalHeaderOffset + $OptionalHeaderSize) -gt $Stream.Length) {
            $Failures.Add("$Label optional header extends beyond file length: $FullPath")
            return $false
        }

        $Stream.Position = $OptionalHeaderOffset
        $OptionalMagic = $Reader.ReadUInt16()
        if ($OptionalMagic -notin @(0x010B, 0x020B)) {
            $Failures.Add(("$Label uses unexpected PE optional header magic 0x{0:X4}: {1}" -f $OptionalMagic, $FullPath))
            return $false
        }

        Write-Host "[ok] $Label PE header validated ($($Item.Length) bytes): $FullPath"
        return $true
    }
    finally {
        $Reader.Dispose()
        $Stream.Dispose()
    }
}

function Test-SelfExtractingExecutable([string]$Path) {
    Test-PEExecutableHeader -Label "self-extracting executable" -Path $Path -MinimumBytes $MinimumSelfExtractingExeBytes | Out-Null
}

function ConvertTo-SmokeSafeName([string]$Value) {
    $Safe = $Value -replace '[^A-Za-z0-9_.-]', '-'
    if ([string]::IsNullOrWhiteSpace($Safe)) {
        return "smoke"
    }
    return $Safe.Trim('-')
}

function Join-SmokeArguments([string[]]$Arguments) {
    if ($null -eq $Arguments -or $Arguments.Count -eq 0) {
        return ""
    }
    $Quoted = foreach ($ArgumentValue in $Arguments) {
        $Argument = [string]$ArgumentValue
        if ($Argument -match '^[A-Za-z0-9_./:=+-]+$') {
            $Argument
        }
        else {
            '"' + ($Argument -replace '"', '\"') + '"'
        }
    }
    return ($Quoted -join " ")
}

function New-SmokeLogSet([string]$Label) {
    New-Item -ItemType Directory -Path $SmokeLogRoot -Force | Out-Null
    $script:SmokeRunIndex += 1
    $SafeLabel = ConvertTo-SmokeSafeName $Label
    $Prefix = "{0:00}-{1}" -f $script:SmokeRunIndex, $SafeLabel
    return [pscustomobject]@{
        StdOut = Join-Path $SmokeLogRoot "$Prefix.stdout.log"
        StdErr = Join-Path $SmokeLogRoot "$Prefix.stderr.log"
    }
}

function Save-SmokeProcessLogs([object]$Run) {
    if ($null -eq $Run -or $Run.LogsSaved) {
        return
    }
    $StdOut = if ($Run.StdOutBuffer) { $Run.StdOutBuffer.ToString() } else { "" }
    $StdErr = if ($Run.StdErrBuffer) { $Run.StdErrBuffer.ToString() } else { "" }
    Set-Content -LiteralPath $Run.StdOut -Value $StdOut -Encoding UTF8
    Set-Content -LiteralPath $Run.StdErr -Value $StdErr -Encoding UTF8
    $Run.LogsSaved = $true
}

function Add-SmokeProcessLogNote {
    param(
        [object]$Run,
        [string]$Message
    )

    if ($null -eq $Run -or $null -eq $Run.StdErrBuffer -or [string]::IsNullOrWhiteSpace($Message)) {
        return
    }
    [void]$Run.StdErrBuffer.AppendLine("[smoke] $Message")
}

function Format-SmokeEnvironmentValue {
    param(
        [string]$Key,
        [string]$Value
    )

    if ($Key -match '(?i)(TOKEN|SECRET|KEY|PASSWORD)') {
        if ([string]::IsNullOrEmpty($Value)) {
            return "<empty>"
        }
        return "<redacted>"
    }
    return $Value
}

function Add-SmokeProcessMetadata {
    param(
        [System.Text.StringBuilder]$Buffer,
        [string]$ExecutablePath,
        [string]$WorkingDirectory,
        [string]$Arguments,
        [hashtable]$Environment,
        [int]$ProcessId,
        [int[]]$BaselineProcessIds = @()
    )

    [void]$Buffer.AppendLine("[smoke] executable: $ExecutablePath")
    [void]$Buffer.AppendLine("[smoke] process id: $ProcessId")
    [void]$Buffer.AppendLine("[smoke] working directory: $WorkingDirectory")
    if ([string]::IsNullOrWhiteSpace($Arguments)) {
        [void]$Buffer.AppendLine("[smoke] arguments: <none>")
    }
    else {
        [void]$Buffer.AppendLine("[smoke] arguments: $Arguments")
    }
    $BaselineText = if ($BaselineProcessIds.Count -gt 0) { $BaselineProcessIds -join "," } else { "<none>" }
    [void]$Buffer.AppendLine("[smoke] baseline executable process ids: $BaselineText")
    foreach ($Key in ($Environment.Keys | Sort-Object)) {
        $Value = Format-SmokeEnvironmentValue -Key $Key -Value ([string]$Environment[$Key])
        [void]$Buffer.AppendLine("[smoke] env $Key=$Value")
    }
}

function Get-SmokeExecutableProcessIds {
    param([string]$ExecutablePath)

    if ([string]::IsNullOrWhiteSpace($ExecutablePath)) {
        return @()
    }

    $FullPath = Resolve-ProjectPath $ExecutablePath
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
                $_.ExecutablePath -and
                $_.ExecutablePath.Equals($FullPath, [System.StringComparison]::OrdinalIgnoreCase)
            } | ForEach-Object { [int]$_.ProcessId } | Select-Object -Unique)
    }
    catch {
        return @()
    }
}

function Remove-SmokeProcessOutputHandlers([object]$Run) {
    if ($null -eq $Run -or -not $Run.Started -or $Run.OutputHandlersRemoved) {
        return
    }
    try {
        $Run.Process.CancelOutputRead()
    }
    catch {
    }
    try {
        $Run.Process.CancelErrorRead()
    }
    catch {
    }
    try {
        if ($Run.StdOutHandler) {
            $Run.Process.remove_OutputDataReceived($Run.StdOutHandler)
        }
        if ($Run.StdErrHandler) {
            $Run.Process.remove_ErrorDataReceived($Run.StdErrHandler)
        }
    }
    catch {
    }
    $Run.OutputHandlersRemoved = $true
}

function Start-SmokeProcess {
    param(
        [string]$Label,
        [string]$ExecutablePath,
        [string[]]$Arguments = @(),
        [hashtable]$Environment = @{},
        [string]$WorkingDirectory = ""
    )

    $FullPath = Resolve-ProjectPath $ExecutablePath
    $BaselineExecutableProcessIds = @(Get-SmokeExecutableProcessIds -ExecutablePath $FullPath)
    $Logs = New-SmokeLogSet $Label
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        return [pscustomobject]@{
            Started = $false
            Error = "executable missing: $FullPath"
            StdOut = $Logs.StdOut
            StdErr = $Logs.StdErr
        }
    }

    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $FullPath
    $StartInfo.Arguments = Join-SmokeArguments $Arguments
    if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
        $StartInfo.WorkingDirectory = Split-Path -Parent $FullPath
    }
    else {
        $StartInfo.WorkingDirectory = Resolve-ProjectPath $WorkingDirectory
    }
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $CaptureOutput = $false
    $StartInfo.RedirectStandardOutput = $CaptureOutput
    $StartInfo.RedirectStandardError = $CaptureOutput
    foreach ($Key in $Environment.Keys) {
        $StartInfo.EnvironmentVariables[$Key] = [string]$Environment[$Key]
    }

    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = $StartInfo
    try {
        [void]$Process.Start()
    }
    catch {
        return [pscustomobject]@{
            Started = $false
            Error = $_.Exception.Message
            StdOut = $Logs.StdOut
            StdErr = $Logs.StdErr
        }
    }

    $StdOutBuffer = New-Object System.Text.StringBuilder
    $StdErrBuffer = New-Object System.Text.StringBuilder
    $StdOutHandler = $null
    $StdErrHandler = $null
    if ($CaptureOutput) {
        $StdOutHandler = [System.Diagnostics.DataReceivedEventHandler]{
            param($Sender, $EventArgs)
            if ($null -ne $EventArgs.Data) {
                [void]$StdOutBuffer.AppendLine($EventArgs.Data)
            }
        }.GetNewClosure()
        $StdErrHandler = [System.Diagnostics.DataReceivedEventHandler]{
            param($Sender, $EventArgs)
            if ($null -ne $EventArgs.Data) {
                [void]$StdErrBuffer.AppendLine($EventArgs.Data)
            }
        }.GetNewClosure()
        $Process.add_OutputDataReceived($StdOutHandler)
        $Process.add_ErrorDataReceived($StdErrHandler)
        $Process.BeginOutputReadLine()
        $Process.BeginErrorReadLine()
    }
    else {
        [void]$StdOutBuffer.AppendLine("[stdout capture disabled for executable smoke]")
        [void]$StdErrBuffer.AppendLine("[stderr capture disabled for executable smoke]")
    }
    Add-SmokeProcessMetadata `
        -Buffer $StdErrBuffer `
        -ExecutablePath $FullPath `
        -WorkingDirectory $StartInfo.WorkingDirectory `
        -Arguments $StartInfo.Arguments `
        -Environment $Environment `
        -ProcessId $Process.Id `
        -BaselineProcessIds $BaselineExecutableProcessIds

    return [pscustomobject]@{
        Started = $true
        Process = $Process
        ExecutablePath = $FullPath
        BaselineExecutableProcessIds = $BaselineExecutableProcessIds
        StdOut = $Logs.StdOut
        StdErr = $Logs.StdErr
        StdOutBuffer = $StdOutBuffer
        StdErrBuffer = $StdErrBuffer
        StdOutHandler = $StdOutHandler
        StdErrHandler = $StdErrHandler
        LogsSaved = $false
        OutputHandlersRemoved = $false
    }
}

function Get-SmokeProcessTreeIds {
    param([int]$RootProcessId)

    $ProcessRows = @()
    try {
        $ProcessRows = @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId, ParentProcessId)
    }
    catch {
        return @()
    }
    $ChildrenByParent = @{}
    foreach ($ProcessRow in $ProcessRows) {
        if (-not $ChildrenByParent.ContainsKey($ProcessRow.ParentProcessId)) {
            $ChildrenByParent[$ProcessRow.ParentProcessId] = @()
        }
        $ChildrenByParent[$ProcessRow.ParentProcessId] += [int]$ProcessRow.ProcessId
    }

    $ProcessIds = New-Object System.Collections.Generic.List[int]
    function Add-ChildProcessIds([int]$CurrentProcessId) {
        if (-not $ChildrenByParent.ContainsKey($CurrentProcessId)) {
            return
        }
        foreach ($ChildProcessId in $ChildrenByParent[$CurrentProcessId]) {
            Add-ChildProcessIds $ChildProcessId
            $ProcessIds.Add($ChildProcessId)
        }
    }

    Add-ChildProcessIds $RootProcessId
    return @($ProcessIds | Select-Object -Unique)
}

function Stop-SmokeExecutableInstances {
    param(
        [string]$ExecutablePath,
        [int[]]$ExceptProcessIds = @(),
        [int[]]$BaselineProcessIds = @()
    )

    if ([string]::IsNullOrWhiteSpace($ExecutablePath)) {
        return @()
    }

    $FullPath = Resolve-ProjectPath $ExecutablePath
    $ExcludedProcessIds = New-Object System.Collections.Generic.HashSet[int]
    foreach ($ProcessId in @($ExceptProcessIds + $BaselineProcessIds)) {
        [void]$ExcludedProcessIds.Add([int]$ProcessId)
    }
    $StoppedProcessIds = New-Object System.Collections.Generic.List[int]
    try {
        $Processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
                $_.ExecutablePath -and
                $_.ExecutablePath.Equals($FullPath, [System.StringComparison]::OrdinalIgnoreCase) -and
                -not $ExcludedProcessIds.Contains([int]$_.ProcessId)
            })
    }
    catch {
        return @()
    }
    foreach ($ProcessRow in $Processes) {
        $ProcessId = [int]$ProcessRow.ProcessId
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        $StoppedProcessIds.Add($ProcessId)
    }
    return @($StoppedProcessIds | Select-Object -Unique)
}

function Stop-SmokeProcessTree {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$ExecutablePath = "",
        [int[]]$BaselineProcessIds = @()
    )

    if ($null -eq $Process) {
        return
    }

    $ChildProcessIds = @(Get-SmokeProcessTreeIds -RootProcessId $Process.Id)
    foreach ($ChildProcessId in $ChildProcessIds) {
        Stop-Process -Id $ChildProcessId -Force -ErrorAction SilentlyContinue
    }
    try {
        if (-not $Process.HasExited) {
            $Process.Kill()
        }
    }
    catch {
    }
    if (-not [string]::IsNullOrWhiteSpace($ExecutablePath)) {
        Stop-SmokeExecutableInstances -ExecutablePath $ExecutablePath -ExceptProcessIds @($Process.Id) -BaselineProcessIds $BaselineProcessIds | Out-Null
    }
    try {
        [void]$Process.WaitForExit(5000)
    }
    catch {
    }
}

function Stop-SmokeProcess([object]$Run) {
    if ($null -eq $Run -or -not $Run.Started) {
        return
    }
    try {
        if (-not $Run.Process.HasExited) {
            Stop-SmokeProcessTree -Process $Run.Process -ExecutablePath $Run.ExecutablePath -BaselineProcessIds $Run.BaselineExecutableProcessIds
        }
        else {
            Stop-SmokeExecutableInstances -ExecutablePath $Run.ExecutablePath -ExceptProcessIds @($Run.Process.Id) -BaselineProcessIds $Run.BaselineExecutableProcessIds | Out-Null
        }
        Start-Sleep -Milliseconds 100
        Save-SmokeProcessLogs $Run
    }
    finally {
        Remove-SmokeProcessOutputHandlers $Run
        $Run.Process.Dispose()
    }
}

function Invoke-SmokeProcessAndWait {
    param(
        [string]$Label,
        [string]$ExecutablePath,
        [string[]]$Arguments = @(),
        [hashtable]$Environment = @{},
        [int]$TimeoutSeconds
    )

    $Run = Start-SmokeProcess -Label $Label -ExecutablePath $ExecutablePath -Arguments $Arguments -Environment $Environment
    if (-not $Run.Started) {
        return [pscustomobject]@{
            Started = $false
            Error = $Run.Error
            StdOut = $Run.StdOut
            StdErr = $Run.StdErr
        }
    }

    $Exited = $Run.Process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $Exited) {
        Stop-SmokeProcess $Run
        return [pscustomobject]@{
            Started = $true
            TimedOut = $true
            ExitCode = $null
            StdOut = $Run.StdOut
            StdErr = $Run.StdErr
        }
    }

    $ChildProcessIds = @(Get-SmokeProcessTreeIds -RootProcessId $Run.Process.Id)
    foreach ($ChildProcessId in $ChildProcessIds) {
        Stop-Process -Id $ChildProcessId -Force -ErrorAction SilentlyContinue
    }
    $ExecutableProcessIds = @(Stop-SmokeExecutableInstances -ExecutablePath $Run.ExecutablePath -ExceptProcessIds @($Run.Process.Id) -BaselineProcessIds $Run.BaselineExecutableProcessIds)
    Start-Sleep -Milliseconds 100
    Save-SmokeProcessLogs $Run
    $ExitCode = $Run.Process.ExitCode
    Remove-SmokeProcessOutputHandlers $Run
    $Run.Process.Dispose()
    return [pscustomobject]@{
        Started = $true
        TimedOut = $false
        ExitCode = $ExitCode
        HadChildProcesses = ($ChildProcessIds.Count + $ExecutableProcessIds.Count) -gt 0
        StdOut = $Run.StdOut
        StdErr = $Run.StdErr
    }
}

function Get-FreeTcpPort {
    $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), 0)
    try {
        $Listener.Start()
        return ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
    }
    finally {
        $Listener.Stop()
    }
}

function New-BackendSmokeEnvironment([string]$Label, [int]$BackendPort) {
    $SafeLabel = ConvertTo-SmokeSafeName $Label
    $StateRoot = Join-Path $SmokeLogRoot ("state-$SafeLabel-$([guid]::NewGuid().ToString('N'))")
    $DataRoot = Join-Path $StateRoot "data"
    New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
    $FullBackendPort = Get-FreeTcpPort
    return @{
        LENGRVIS_BACKEND_HOST = "127.0.0.1"
        LENGRVIS_BACKEND_PORT = [string]$BackendPort
        LENGRVIS_GUARDIAN_PORT = [string]$BackendPort
        LENGRVIS_FULL_BACKEND = "0"
        LENGRVIS_FULL_BACKEND_PORT = [string]$FullBackendPort
        LENGRVIS_FULL_BACKEND_URL = "http://127.0.0.1:$FullBackendPort"
        LENGRVIS_CONFIG_DIR = $StateRoot
        LENGRVIS_DATA_DIR = $DataRoot
        LENGRVIS_PROVIDER_NAME = "mock"
        LENGRVIS_API_KEY = ""
        LENGRVIS_BACKEND_LOG_LEVEL = "warning"
        LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL = "1"
    }
}

function Invoke-BackendHealthSmoke {
    param(
        [string]$Label,
        [string]$ExecutablePath
    )

    $BackendPort = Get-FreeTcpPort
    $Environment = New-BackendSmokeEnvironment -Label "$Label-health" -BackendPort $BackendPort
    $HealthUrl = "http://127.0.0.1:$BackendPort/health"
    $Run = Start-SmokeProcess -Label "$Label health" -ExecutablePath $ExecutablePath -Environment $Environment
    if (-not $Run.Started) {
        return [pscustomobject]@{
            Passed = $false
            Diagnostic = "health process could not start for $HealthUrl`: $($Run.Error) (stdout log: $($Run.StdOut); stderr log: $($Run.StdErr))"
        }
    }

    Add-SmokeProcessLogNote -Run $Run -Message "health url: $HealthUrl"
    Add-SmokeProcessLogNote -Run $Run -Message "smoke timeout seconds: $SmokeTimeoutSeconds"
    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $LastProbeDiagnostic = "no probe attempted"
    try {
        while ($Stopwatch.Elapsed.TotalSeconds -lt $SmokeTimeoutSeconds) {
            if ($Run.Process.HasExited) {
                $ChildProcessIds = @(Get-SmokeProcessTreeIds -RootProcessId $Run.Process.Id)
                if ($ChildProcessIds.Count -eq 0) {
                    Save-SmokeProcessLogs $Run
                    return [pscustomobject]@{
                        Passed = $false
                        Diagnostic = "health process exited before $HealthUrl responded (exit $($Run.Process.ExitCode); last probe: $LastProbeDiagnostic; stdout log: $($Run.StdOut); stderr log: $($Run.StdErr))"
                    }
                }
            }
            try {
                $RemainingSeconds = [Math]::Max(1, [Math]::Ceiling($SmokeTimeoutSeconds - $Stopwatch.Elapsed.TotalSeconds))
                $ProbeTimeoutSeconds = [Math]::Min(2, [int]$RemainingSeconds)
                $Response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec $ProbeTimeoutSeconds
                if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 300) {
                    Write-Host "[ok] $Label runnable smoke served $HealthUrl"
                    return [pscustomobject]@{ Passed = $true; Diagnostic = "" }
                }
                $LastProbeDiagnostic = "HTTP $($Response.StatusCode)"
            }
            catch {
                $LastProbeDiagnostic = $_.Exception.Message
                Start-Sleep -Milliseconds 500
            }
        }
        return [pscustomobject]@{
            Passed = $false
            Diagnostic = "health endpoint did not respond within $SmokeTimeoutSeconds seconds: $HealthUrl (last probe: $LastProbeDiagnostic; stdout log: $($Run.StdOut); stderr log: $($Run.StdErr))"
        }
    }
    finally {
        Stop-SmokeProcess $Run
    }
}

function Invoke-BackendExecutableSmoke {
    param(
        [string]$Label,
        [string]$ExecutablePath
    )

    $FullPath = Resolve-ProjectPath $ExecutablePath
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        return
    }

    Write-Host "[info] probing $Label through isolated /health"
    $HealthSmoke = Invoke-BackendHealthSmoke -Label $Label -ExecutablePath $FullPath
    if (-not $HealthSmoke.Passed) {
        $Failures.Add("$Label runnable smoke failed. Health probe: $($HealthSmoke.Diagnostic). Smoke logs: $SmokeLogRoot")
    }
}

function Get-DirectorySummary {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [ordered]@{ present = $false; files = 0; bytes = 0; sha256 = "" }
    }
    $rootPath = (Resolve-Path -LiteralPath $Path).Path
    $files = Get-ChildItem -LiteralPath $Path -File -Recurse -Force | Sort-Object FullName
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($rootPath.Length).TrimStart('\', '/')
            $nameBytes = [System.Text.Encoding]::UTF8.GetBytes($relative.ToLowerInvariant())
            $hash.TransformBlock($nameBytes, 0, $nameBytes.Length, $null, 0) | Out-Null
            $content = [System.IO.File]::ReadAllBytes($file.FullName)
            $hash.TransformBlock($content, 0, $content.Length, $null, 0) | Out-Null
        }
        $hash.TransformFinalBlock([byte[]]::new(0), 0, 0) | Out-Null
        $digest = -join ($hash.Hash | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $hash.Dispose()
    }
    return [ordered]@{
        present = $true
        files = @($files).Count
        bytes = [int64](($files | Measure-Object -Property Length -Sum).Sum)
        sha256 = $digest
    }
}

function Compare-Summary([object]$Expected, [object]$Actual, [string]$Label) {
    if ([bool]$Expected.present -ne [bool]$Actual.present) { $Failures.Add("$Label manifest present flag does not match package files."); return }
    if ([int64]$Expected.files -ne [int64]$Actual.files) { $Failures.Add("$Label manifest file count does not match package files."); return }
    if ([int64]$Expected.bytes -ne [int64]$Actual.bytes) { $Failures.Add("$Label manifest byte count does not match package files."); return }
    if ([string]$Expected.sha256 -ne [string]$Actual.sha256) { $Failures.Add("$Label manifest sha256 does not match package files."); return }
    Write-Host "[ok] $Label manifest summary matches package files"
}

function Test-OllamaBundleManifest([string]$ManifestPath, [string]$RuntimeDir, [string]$ModelsDir) {
    $FullManifestPath = Resolve-ProjectPath $ManifestPath
    $FullRuntimeDir = Resolve-ProjectPath $RuntimeDir
    $FullModelsDir = Resolve-ProjectPath $ModelsDir
    if (-not (Test-Path -LiteralPath $FullManifestPath -PathType Leaf)) {
        $Failures.Add("portable Ollama bundle manifest missing: $FullManifestPath")
        return
    }
    try {
        $Manifest = Get-Content -LiteralPath $FullManifestPath -Raw | ConvertFrom-Json
    }
    catch {
        $Failures.Add("portable Ollama bundle manifest is not valid JSON: $FullManifestPath")
        return
    }
    if ([int]$Manifest.schema -ne 1) { $Failures.Add("portable Ollama bundle manifest has unsupported schema: $($Manifest.schema)") }
    if (-not [bool]$Manifest.accepted_licenses) { $Failures.Add("portable Ollama bundle manifest must confirm accepted_licenses=true.") }
    if (-not [string]$Manifest.model) { $Failures.Add("portable Ollama bundle manifest must record the packaged model.") }
    if ([string]$Manifest.models.model_manifest) {
        $ModelManifestPath = Join-Path $FullModelsDir ([string]$Manifest.models.model_manifest)
        if (-not (Test-Path -LiteralPath $ModelManifestPath -PathType Leaf)) {
            $Failures.Add("portable Ollama model manifest missing: $ModelManifestPath")
        }
    }
    Compare-Summary -Expected $Manifest.runtime.summary -Actual (Get-DirectorySummary -Path $FullRuntimeDir) -Label "Ollama runtime"
    Compare-Summary -Expected $Manifest.models.summary -Actual (Get-DirectorySummary -Path $FullModelsDir) -Label "Ollama models"
}

$DistPath = Resolve-ProjectPath $DistDir
$PortablePath = Resolve-ProjectPath $PortableDir
$PortableZipPath = Resolve-ProjectPath $PortableZip
$SelfExtractingPath = Resolve-ProjectPath $SelfExtractingExe
$PortableOllamaDir = Join-Path $PortablePath "resources\ollama"
$PortableOllamaModelsDir = Join-Path $PortablePath "resources\ollama-models"
$PortableOllamaManifest = Join-Path $PortablePath "resources\ollama-bundle-manifest.json"
$BackendExePath = Join-Path $DistPath "backend.exe"
$PortableLauncherPath = Join-Path $PortablePath "Lengrvis.exe"
$PortableBackendExePath = Join-Path $PortablePath "resources\backend\backend.exe"
$PortableAppDistPath = Join-Path $PortablePath "resources\app\dist"

Test-RequiredDirectory "dist directory" $DistPath
Test-RequiredFile "backend executable" $BackendExePath
Test-RequiredDirectory "portable directory" $PortablePath
Test-RequiredFile "portable launcher" $PortableLauncherPath
Test-RequiredFile "portable backend executable" $PortableBackendExePath
Test-RequiredDirectory "portable app resources" (Join-Path $PortablePath "resources\app")
Test-RequiredDirectory "portable renderer dist" $PortableAppDistPath
Test-RequiredFile "portable app package manifest" (Join-Path $PortablePath "resources\app\package.json")
Test-RequiredFile "portable zip" $PortableZipPath
Test-RequiredFile "self-extracting executable" $SelfExtractingPath
Test-PEExecutableHeader -Label "backend executable" -Path $BackendExePath -MinimumBytes $MinimumBackendExecutableBytes | Out-Null
$PortableLauncherPreflightPassed = Test-PEExecutableHeader -Label "portable launcher" -Path $PortableLauncherPath -MinimumBytes $MinimumPortableLauncherBytes
Test-PEExecutableHeader -Label "portable backend executable" -Path $PortableBackendExePath -MinimumBytes $MinimumBackendExecutableBytes | Out-Null
if ($PortableLauncherPreflightPassed) {
    Write-Host "[ok] portable launcher startup preflight completed without opening the GUI"
}
Test-SelfExtractingExecutable $SelfExtractingPath
if ($RequireBundledOllama) {
    Test-NonEmptyDirectory "portable Ollama runtime" $PortableOllamaDir
    Test-NonEmptyDirectory "portable Ollama models" $PortableOllamaModelsDir
    Test-RequiredFile "portable Ollama bundle manifest" $PortableOllamaManifest
    Test-OllamaBundleManifest -ManifestPath $PortableOllamaManifest -RuntimeDir $PortableOllamaDir -ModelsDir $PortableOllamaModelsDir
}
Test-ReleaseSourceMapFreeDirectory -Label "portable app dist" -Path $PortableAppDistPath

if (Test-Path -LiteralPath $PortableZipPath -PathType Leaf) {
    $Zip = [System.IO.Compression.ZipFile]::OpenRead($PortableZipPath)
    try {
        Test-ZipEntry $Zip "Lengrvis.exe"
        Test-ZipEntry $Zip "resources/backend/backend.exe"
        Test-ZipEntry $Zip "resources/app/package.json"
        Test-ZipReleaseSourceMapFree -Zip $Zip -Label "portable zip app dist" -Prefix "resources/app/dist"
        if ($RequireBundledOllama) {
            Test-ZipEntry $Zip "resources/ollama-bundle-manifest.json"
            Test-ZipDirectoryEntry $Zip "resources/ollama"
            Test-ZipDirectoryEntry $Zip "resources/ollama-models"
        }
    }
    finally {
        $Zip.Dispose()
    }
}

if ($RunExecutableSmoke) {
    Invoke-BackendExecutableSmoke -Label "backend executable" -ExecutablePath $BackendExePath
    Invoke-BackendExecutableSmoke -Label "portable backend executable" -ExecutablePath $PortableBackendExePath
}
else {
    Write-Host "[info] executable runnable smoke not requested; this is a structural-only packaging check. Pass -RunExecutableSmoke for release-candidate packaging validation."
}

if ($Failures.Count -gt 0) {
    Write-Host ""
    Write-Host "Packaging verification failed:" -ForegroundColor Red
    foreach ($Failure in $Failures) {
        Write-Host " - $Failure" -ForegroundColor Red
    }
    if ($MissingArtifacts.Count -gt 0) {
        Write-Host ""
        Write-Host "Missing release artifacts are blocking the gate:" -ForegroundColor Yellow
        foreach ($Artifact in $MissingArtifacts) {
            Write-Host " - $($Artifact.Label) ($($Artifact.Type)): $($Artifact.Path)" -ForegroundColor Yellow
        }
        Write-Host ""
        Write-Host "This verification step does not generate artifacts. Run a full packaging build first, or pass -DistDir/-PortableDir/-PortableZip/-SelfExtractingExe to the artifacts you intend to release." -ForegroundColor Yellow
        Write-Host "Suggested build command: .\scripts\build_all.ps1" -ForegroundColor Yellow
        Write-Host "Verify existing artifacts: .\scripts\build_all.ps1 -VerifyOnly" -ForegroundColor Yellow
        Write-Host "Verify runnable artifacts: .\scripts\verify_packaging.ps1 -RunExecutableSmoke -SmokeTimeoutSeconds $SmokeTimeoutSeconds" -ForegroundColor Yellow
        if ($RequireBundledOllama) {
            Write-Host "Bundled Ollama releases must prepare resources before verification; see .\scripts\prepare_ollama_release.ps1." -ForegroundColor Yellow
        }
    }
    if (Test-Path -LiteralPath $SmokeLogRoot -PathType Container) {
        Write-Host ""
        Write-Host "Executable smoke logs: $SmokeLogRoot" -ForegroundColor Yellow
    }
    exit 1
}

Write-Host ""
Write-Host "Packaging verification passed." -ForegroundColor Green
