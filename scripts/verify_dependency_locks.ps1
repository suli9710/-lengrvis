[CmdletBinding()]
param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

function Normalize-PythonPackageName {
    param([Parameter(Mandatory = $true)][string]$Name)

    return ($Name.ToLowerInvariant() -replace "[-_.]+", "-")
}

function Remove-RequirementComment {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)

    $hashIndex = $Line.IndexOf("#")
    if ($hashIndex -lt 0) {
        return $Line.Trim()
    }

    return $Line.Substring(0, $hashIndex).Trim()
}

function Get-PythonRequirementName {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)

    $cleanLine = Remove-RequirementComment -Line $Line
    if ([string]::IsNullOrWhiteSpace($cleanLine)) {
        return $null
    }
    if ($cleanLine.StartsWith("-")) {
        return $null
    }

    $namePattern = "^\s*(?<name>[A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*(?:===|==|~=|!=|<=|>=|<|>|;|$)"
    $match = [regex]::Match($cleanLine, $namePattern)
    if (-not $match.Success) {
        return $null
    }

    return $match.Groups["name"].Value
}

function Get-PinnedPythonLockEntries {
    param([Parameter(Mandatory = $true)][string]$LockPath)

    $entries = @{}
    $unpinned = New-Object System.Collections.Generic.List[string]

    foreach ($line in Get-Content -LiteralPath $LockPath) {
        $cleanLine = Remove-RequirementComment -Line $line
        if ([string]::IsNullOrWhiteSpace($cleanLine) -or $cleanLine.StartsWith("-")) {
            continue
        }

        $name = Get-PythonRequirementName -Line $cleanLine
        if ($null -eq $name) {
            continue
        }

        $pinPattern = "^\s*(?<name>[A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*==\s*(?<version>[^\s;]+)"
        $pinMatch = [regex]::Match($cleanLine, $pinPattern)
        if (-not $pinMatch.Success) {
            $unpinned.Add($cleanLine)
            continue
        }

        $normalizedName = Normalize-PythonPackageName -Name $pinMatch.Groups["name"].Value
        $entries[$normalizedName] = [pscustomobject]@{
            Name = $pinMatch.Groups["name"].Value
            Version = $pinMatch.Groups["version"].Value
            Line = $cleanLine
        }
    }

    return [pscustomobject]@{
        Entries = $entries
        Unpinned = [string[]]$unpinned
    }
}

function Test-BackendPythonLocks {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [System.Collections.Generic.List[string]]$Issues
    )

    $requirementsPath = Join-Path $Root "backend\requirements.txt"
    $lockPath = Join-Path $Root "backend\requirements-lock.txt"
    $constraintsPath = Join-Path $Root "backend\constraints.txt"

    if (-not (Test-Path -LiteralPath $requirementsPath)) {
        $Issues.Add("Missing backend requirements file: backend/requirements.txt")
        return
    }

    if (Test-Path -LiteralPath $lockPath) {
        $selectedLockPath = $lockPath
        $selectedLockLabel = "backend/requirements-lock.txt"
    }
    elseif (Test-Path -LiteralPath $constraintsPath) {
        $selectedLockPath = $constraintsPath
        $selectedLockLabel = "backend/constraints.txt"
    }
    else {
        $Issues.Add("Missing backend dependency lock: expected backend/requirements-lock.txt or backend/constraints.txt")
        return
    }

    $lockResult = Get-PinnedPythonLockEntries -LockPath $selectedLockPath
    foreach ($unpinnedLine in $lockResult.Unpinned) {
        $Issues.Add("${selectedLockLabel} contains an unpinned dependency: $unpinnedLine")
    }

    $requirements = New-Object System.Collections.Generic.List[string]
    foreach ($line in Get-Content -LiteralPath $requirementsPath) {
        $name = Get-PythonRequirementName -Line $line
        if ($null -eq $name) {
            continue
        }
        $requirements.Add($name)
    }

    foreach ($name in $requirements) {
        $normalizedName = Normalize-PythonPackageName -Name $name
        if (-not $lockResult.Entries.ContainsKey($normalizedName)) {
            $Issues.Add("backend/requirements.txt direct dependency '$name' is missing a pinned == entry in $selectedLockLabel")
        }
    }

    if ($lockResult.Unpinned.Count -eq 0) {
        Write-Host "Verified backend direct dependency pins in $selectedLockLabel ($($requirements.Count) requirements)."
    }
}

function Test-NpmPackageLock {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$PackageDir,
        [System.Collections.Generic.List[string]]$Issues
    )

    $packageJsonPath = Join-Path $Root "$PackageDir\package.json"
    $packageLockPath = Join-Path $Root "$PackageDir\package-lock.json"

    if (-not (Test-Path -LiteralPath $packageJsonPath)) {
        $Issues.Add("Missing npm package manifest: $PackageDir/package.json")
        return
    }

    if (-not (Test-Path -LiteralPath $packageLockPath)) {
        $Issues.Add("Missing npm package lock: $PackageDir/package-lock.json")
        return
    }

    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $node) {
        $Issues.Add("Node.js is required to parse $PackageDir/package-lock.json")
        return
    }

    $nodeScript = @'
const fs = require("fs");
const [packageJsonPath, packageLockPath] = process.argv.slice(2);
const manifest = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
const lock = JSON.parse(fs.readFileSync(packageLockPath, "utf8"));
const rootPackage = lock.packages && lock.packages[""] ? lock.packages[""] : lock;
const issues = [];
const dependencySections = ["dependencies", "devDependencies", "optionalDependencies"];

if (!lock.lockfileVersion) {
  issues.push("missing lockfileVersion");
}
if (rootPackage.name !== manifest.name) {
  issues.push(`root package name ${rootPackage.name || "<missing>"} does not match package.json ${manifest.name || "<missing>"}`);
}
if (rootPackage.version !== manifest.version) {
  issues.push(`root package version ${rootPackage.version || "<missing>"} does not match package.json ${manifest.version || "<missing>"}`);
}
for (const section of dependencySections) {
  const manifestDeps = manifest[section] || {};
  const lockRootDeps = rootPackage[section] || {};
  for (const [name, spec] of Object.entries(manifestDeps)) {
    if (lockRootDeps[name] !== spec) {
      issues.push(`${section}.${name} spec ${lockRootDeps[name] || "<missing>"} does not match package.json ${spec}`);
    }
    const packageEntry = lock.packages && lock.packages[`node_modules/${name}`];
    if (!packageEntry) {
      issues.push(`${section}.${name} is missing from lock packages`);
    }
  }
}

if (issues.length) {
  for (const issue of issues) {
    console.error(issue);
  }
  process.exit(1);
}

console.log(`${packageLockPath}: ${rootPackage.name}@${rootPackage.version} lockfileVersion=${lock.lockfileVersion}`);
'@

    $nodeOutput = $nodeScript | node - $packageJsonPath $packageLockPath 2>&1
    if ($LASTEXITCODE -ne 0) {
        foreach ($line in $nodeOutput) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                $Issues.Add("$PackageDir/package-lock.json failed validation: $line")
            }
        }
        return
    }

    foreach ($line in $nodeOutput) {
        Write-Host $line
    }
}

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$issues = New-Object System.Collections.Generic.List[string]

Test-BackendPythonLocks -Root $resolvedRoot -Issues $issues
Test-NpmPackageLock -Root $resolvedRoot -PackageDir "desktop" -Issues $issues
Test-NpmPackageLock -Root $resolvedRoot -PackageDir "mobile" -Issues $issues

if ($issues.Count -gt 0) {
    Write-Host "Dependency lock verification failed:" -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host " - $issue" -ForegroundColor Red
    }
    exit 1
}

Write-Host "Dependency lock verification passed."
