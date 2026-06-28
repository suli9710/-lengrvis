param(
    [ValidateSet("auto", "winml", "directml", "openvino", "cpu")]
    [string]$Runtime = "auto",
    [switch]$SkipModels,
    [switch]$SkipSmoke,
    [string]$ModelsDir = "",
    [string]$HfEndpoint = "",
    [string]$HfMirror = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-AccelerationRequirementLines {
    param(
        [string]$RequirementsFile,
        [string]$ResolvedRuntime
    )
    if (-not (Test-Path $RequirementsFile)) {
        throw "Pinned requirements file not found: $RequirementsFile"
    }

    $selected = New-Object System.Collections.Generic.List[string]
    $section = "common"
    $runtimeSectionSeen = $false
    foreach ($line in Get-Content -LiteralPath $RequirementsFile) {
        $trimmed = $line.Trim()
        $sectionMatch = [regex]::Match($trimmed, "^#\s*Runtime:\s*(?<runtime>[A-Za-z0-9_-]+)\s*$")
        if ($sectionMatch.Success) {
            $section = $sectionMatch.Groups["runtime"].Value.ToLowerInvariant()
            if ($section -eq $ResolvedRuntime.ToLowerInvariant()) {
                $runtimeSectionSeen = $true
            }
            continue
        }
        if ($trimmed -match "^#\s*Common packages\s*$") {
            $section = "common"
            continue
        }
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($section -eq "common" -or $section -eq $ResolvedRuntime.ToLowerInvariant()) {
            $selected.Add($trimmed)
        }
    }
    if (-not $runtimeSectionSeen) {
        throw "No acceleration requirements section found for runtime: $ResolvedRuntime"
    }
    return [string[]]$selected
}

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
    if ([string]::IsNullOrWhiteSpace($cleanLine) -or $cleanLine.StartsWith("-")) {
        return $null
    }
    $match = [regex]::Match(
        $cleanLine,
        "^\s*(?<name>[A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*(?:===|==|~=|!=|<=|>=|<|>|;|$)"
    )
    if (-not $match.Success) {
        return $null
    }
    return $match.Groups["name"].Value
}

function Get-HashLockedRequirementBlocks {
    param([Parameter(Mandatory = $true)][string]$LockFile)

    $blocks = @{}
    $currentName = $null
    $currentLines = New-Object System.Collections.Generic.List[string]

    foreach ($line in Get-Content -LiteralPath $LockFile) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        $isContinuation = $line.StartsWith(" ") -or $line.StartsWith("`t")
        if (-not $isContinuation) {
            if ($currentName -and $currentLines.Count -gt 0) {
                $blocks[$currentName] = [string[]]$currentLines
            }
            $currentName = $null
            $currentLines = New-Object System.Collections.Generic.List[string]

            $name = Get-PythonRequirementName -Line $line
            if ($null -eq $name) {
                continue
            }
            $currentName = Normalize-PythonPackageName -Name $name
            $currentLines.Add($line)
            continue
        }
        if ($currentName -and $trimmed -match "^--hash\s*=\s*sha256:[a-fA-F0-9]{64}") {
            $currentLines.Add($line)
        }
    }

    if ($currentName -and $currentLines.Count -gt 0) {
        $blocks[$currentName] = [string[]]$currentLines
    }
    return $blocks
}

function Get-SelectedHashLockedRequirementLines {
    param(
        [Parameter(Mandatory = $true)][string]$LockFile,
        [Parameter(Mandatory = $true)][string[]]$Requirements,
        [Parameter(Mandatory = $true)][string]$ResolvedRuntime
    )

    $blocks = Get-HashLockedRequirementBlocks -LockFile $LockFile
    $runtimePackages = @{
        winml = @("onnxruntime-windowsml", "onnxruntime-genai-winml")
        directml = @("onnxruntime-directml", "onnxruntime-genai-directml")
        openvino = @("onnxruntime-openvino", "openvino", "openvino-telemetry")
        cpu = @("onnxruntime", "onnxruntime-genai")
    }
    $excluded = @{}
    foreach ($runtimeName in $runtimePackages.Keys) {
        if ($runtimeName -eq $ResolvedRuntime.ToLowerInvariant()) {
            continue
        }
        foreach ($packageName in [string[]]$runtimePackages[$runtimeName]) {
            $excluded[(Normalize-PythonPackageName -Name $packageName)] = $true
        }
    }

    foreach ($requirement in $Requirements) {
        $name = Get-PythonRequirementName -Line $requirement
        if ($null -eq $name) {
            continue
        }
        $normalized = Normalize-PythonPackageName -Name $name
        if (-not $blocks.ContainsKey($normalized)) {
            throw "Acceleration hash lock does not contain a pinned hash block for direct requirement: $name"
        }
    }

    $selected = New-Object System.Collections.Generic.List[string]
    foreach ($normalized in ($blocks.Keys | Sort-Object)) {
        if ($excluded.ContainsKey([string]$normalized)) {
            continue
        }
        foreach ($line in [string[]]$blocks[$normalized]) {
            $selected.Add($line)
        }
        $selected.Add("")
    }

    return [string[]]$selected
}

function Invoke-PipHashLockedRequirements {
    param(
        [string]$RequirementsFile,
        [string]$LockFile,
        [string]$ResolvedRuntime
    )
    if (-not (Test-Path $LockFile)) {
        throw "Acceleration hash lock not found: $LockFile. Regenerate with: uv pip compile --generate-hashes --python-version 3.12 --universal --output-file scripts\acceleration-requirements-lock.txt scripts\acceleration-requirements.txt"
    }

    $requirements = Get-AccelerationRequirementLines -RequirementsFile $RequirementsFile -ResolvedRuntime $ResolvedRuntime
    if ($requirements.Count -eq 0) {
        throw "No acceleration requirements selected for runtime: $ResolvedRuntime"
    }
    $hashLockedRequirements = Get-SelectedHashLockedRequirementLines -LockFile $LockFile -Requirements $requirements -ResolvedRuntime $ResolvedRuntime

    $tempRequirements = New-TemporaryFile
    try {
        Set-Content -LiteralPath $tempRequirements.FullName -Value $hashLockedRequirements -Encoding ASCII
        & $Python -m pip install --require-hashes -r $tempRequirements.FullName
        if ($LASTEXITCODE -ne 0) {
            throw "pip install failed for hash-locked acceleration requirements (runtime: $ResolvedRuntime)"
        }
    } finally {
        Remove-Item -LiteralPath $tempRequirements.FullName -Force -ErrorAction SilentlyContinue
    }
}

function Resolve-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Test-WinMlRuntimeAvailable {
    try {
        $devices = Get-CimInstance -ClassName Win32_PnPEntity -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -and $_.Name -match '(?i)NPU|Neural Processing Unit|Compute Accelerator'
            }
        if ($devices) { return $true }
    } catch {
        # Fall through to OS build heuristic.
    }
    try {
        $build = [int](Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).BuildNumber
        return $build -ge 26100
    } catch {
        return $false
    }
}

function Test-DirectMlRuntimeAvailable {
    try {
        $gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -and $_.Name -notmatch '(?i)Microsoft Basic|Remote Desktop|Virtual' }
        return [bool]$gpu
    } catch {
        return $false
    }
}

function Test-OpenVinoRuntimeAvailable {
    try {
        $cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
        return [bool]($cpu -and $cpu.Manufacturer -match '(?i)Intel')
    } catch {
        return $false
    }
}

function Resolve-AutoAccelerationRuntime {
    if (Test-WinMlRuntimeAvailable) { return "winml" }
    if (Test-DirectMlRuntimeAvailable) { return "directml" }
    if (Test-OpenVinoRuntimeAvailable) { return "openvino" }
    return "cpu"
}

function Assert-SupportedAccelerationRuntime {
    param([string]$ResolvedRuntime)
    if ($ResolvedRuntime -notin @("winml", "directml", "openvino", "cpu")) {
        throw "Unsupported acceleration runtime: $ResolvedRuntime"
    }
}

$repoRoot = Resolve-RepoRoot
$backendPath = Join-Path $repoRoot "backend"
if (-not $ModelsDir) {
    $ModelsDir = Join-Path $repoRoot ".lengrvis_data\models"
}
$manifestPath = Join-Path $repoRoot "backend\app\acceleration\model_manifest.json"
$accelerationRequirementsPath = Join-Path $repoRoot "scripts\acceleration-requirements.txt"
$accelerationLockPath = Join-Path $repoRoot "scripts\acceleration-requirements-lock.txt"

# Runtime-specific ONNX Runtime flavors are mutually exclusive. When Runtime=auto,
# probe the host once and install exactly one variant to avoid package conflicts.
$resolvedRuntime = $Runtime
if ($Runtime -eq "auto") {
    $resolvedRuntime = Resolve-AutoAccelerationRuntime
    Write-Host "Auto-detected acceleration runtime: $resolvedRuntime" -ForegroundColor Green
}
Assert-SupportedAccelerationRuntime -ResolvedRuntime $resolvedRuntime

Write-Step "Preparing Python packages (hash-locked, runtime: $resolvedRuntime)"
Invoke-PipHashLockedRequirements -RequirementsFile $accelerationRequirementsPath -LockFile $accelerationLockPath -ResolvedRuntime $resolvedRuntime

if (-not $SkipModels) {
    Write-Step "Downloading model manifests into $ModelsDir"
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    $env:HF_HOME = Join-Path $repoRoot ".lengrvis_data\hf"
    if ($HfEndpoint) { $env:HF_ENDPOINT = $HfEndpoint }
    if ($HfMirror) { $env:HF_ENDPOINT = $HfMirror }
    $env:LENGRVIS_MODELS_DIR = $ModelsDir
    $env:LENGRVIS_MODEL_MANIFEST = $manifestPath
    @'
import json
import hashlib
import os
from pathlib import Path

from huggingface_hub import snapshot_download

def primary_onnx_model(path: Path) -> Path | None:
    for name in ("model.onnx", "embedding.onnx", "vision_model.onnx", "encoder_model.onnx", "det_model.onnx"):
        candidate = path / name
        if candidate.is_file():
            return candidate
    candidates = sorted(item for item in path.rglob("*.onnx") if item.is_file())
    return candidates[0] if candidates else None

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

manifest = json.loads(Path(os.environ["LENGRVIS_MODEL_MANIFEST"]).read_text(encoding="utf-8"))
models_dir = Path(os.environ["LENGRVIS_MODELS_DIR"])
for item in manifest["models"]:
    repo = item.get("repo") or ""
    if not repo or not item.get("recommended", False):
        continue
    # Supply-chain hardening (audit B-3): every downloadable model MUST carry a
    # pinned revision in the manifest. Refuse to download unpinned snapshots.
    revision = (item.get("revision") or "").strip()
    if not revision:
        raise SystemExit(
            f"model '{item['id']}' has no 'revision' pin in the manifest; "
            "refusing to download an unpinned snapshot"
        )
    if revision.lower() in {"main", "master", "head"} or len(revision) != 40:
        raise SystemExit(
            f"model '{item['id']}' revision must be a 40-character commit sha; got {revision!r}"
        )
    if any(ch not in "0123456789abcdef" for ch in revision.lower()):
        raise SystemExit(
            f"model '{item['id']}' revision must be a lowercase hex commit sha; got {revision!r}"
        )
    target = models_dir / item["path"]
    patterns = item.get("patterns") or None
    print(f"Downloading {item['id']} from {repo}@{revision} -> {target}")
    snapshot_path = snapshot_download(
        repo_id=repo,
        revision=revision,
        local_dir=str(target),
        allow_patterns=patterns,
    )
    # Post-download sanity check: the snapshot directory must exist and be
    # non-empty, otherwise abort instead of leaving a broken install behind.
    downloaded = [p for p in Path(snapshot_path).rglob("*") if p.is_file()]
    if not downloaded:
        raise SystemExit(
            f"model '{item['id']}' snapshot at {snapshot_path} is empty after download"
        )
    expected_sha256 = (item.get("model_sha256") or item.get("sha256") or "").strip().lower().removeprefix("sha256:")
    if not expected_sha256:
        raise SystemExit(
            f"model '{item['id']}' has no model_sha256/sha256 pin in the manifest; "
            "refusing to install an unverified model"
        )
    model_file = primary_onnx_model(Path(snapshot_path))
    if model_file is None:
        raise SystemExit(f"model '{item['id']}' snapshot at {snapshot_path} does not contain an ONNX file")
    actual_sha256 = sha256_file(model_file)
    if actual_sha256 != expected_sha256:
        raise SystemExit(
            f"model '{item['id']}' sha256 mismatch for {model_file}: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    print(f"Verified {item['id']}: {len(downloaded)} file(s), {model_file.name} sha256={actual_sha256}")
'@ | & $Python -
}

if (-not $SkipSmoke) {
    Write-Step "Running acceleration smoke"
    $env:LENGRVIS_DATA_DIR = Join-Path $repoRoot ".lengrvis_data"
    $env:LENGRVIS_ONNX_MODELS_DIR = $ModelsDir
    $env:LENGRVIS_ONNX_PROVIDER_PREFERENCE = "winml,directml,openvino,cpu"
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$backendPath;$env:PYTHONPATH" } else { $backendPath }
    @'
from app.config import AppSettings
from app.llm.onnx_provider import health_snapshot, warmup
from app.indexer.local_embedding_provider import test_embedding
from app.indexer.ocr_service import accelerated_ocr_smoke
from app.tools.vision_tools import test_image_embedding

settings = AppSettings.from_sources()
print("status:", health_snapshot(settings))
print("warmup:", warmup(settings))
print("embedding:", test_embedding(settings))
print("ocr:", accelerated_ocr_smoke(settings))
print("image:", test_image_embedding(settings))
'@ | & $Python -
}

Write-Step "Acceleration setup complete"
