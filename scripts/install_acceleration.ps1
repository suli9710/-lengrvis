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

# Supply-chain hardening (audit B-3): all packages are installed from exact
# `==` pins. Floating specs / blanket --upgrade are not allowed here.
function Invoke-Pip {
    param([string[]]$Packages)
    if ($Packages.Count -eq 0) { return }
    & $Python -m pip install @Packages
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed for: $($Packages -join ', ')"
    }
}

function Invoke-PipRequirements {
    param([string]$RequirementsFile)
    if (-not (Test-Path $RequirementsFile)) {
        throw "Pinned requirements file not found: $RequirementsFile"
    }
    & $Python -m pip install -r $RequirementsFile
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed for requirements file: $RequirementsFile"
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

function Install-AccelerationRuntimePackages {
    param([string]$ResolvedRuntime)
    switch ($ResolvedRuntime) {
        "winml" {
            Invoke-Pip @("onnxruntime-windowsml==1.22.0", "onnxruntime-genai-winml==0.8.0")
        }
        "directml" {
            Invoke-Pip @("onnxruntime-directml==1.22.0", "onnxruntime-genai-directml==0.8.0")
        }
        "openvino" {
            Invoke-Pip @("onnxruntime-openvino==1.22.0", "openvino==2025.2.0")
        }
        "cpu" {
            Invoke-Pip @("onnxruntime==1.26.0", "onnxruntime-genai==0.8.0")
        }
        default {
            throw "Unsupported acceleration runtime: $ResolvedRuntime"
        }
    }
}

$repoRoot = Resolve-RepoRoot
$backendPath = Join-Path $repoRoot "backend"
if (-not $ModelsDir) {
    $ModelsDir = Join-Path $repoRoot ".lengrvis_data\models"
}
$manifestPath = Join-Path $repoRoot "backend\app\acceleration\model_manifest.json"

Write-Step "Preparing Python packages (pinned versions)"
Invoke-PipRequirements (Join-Path $repoRoot "scripts\acceleration-requirements.txt")

# Runtime-specific ONNX Runtime flavors are mutually exclusive. When Runtime=auto,
# probe the host once and install exactly one variant to avoid package conflicts.
$resolvedRuntime = $Runtime
if ($Runtime -eq "auto") {
    $resolvedRuntime = Resolve-AutoAccelerationRuntime
    Write-Host "Auto-detected acceleration runtime: $resolvedRuntime" -ForegroundColor Green
}

# Pinned inline with scripts/acceleration-requirements.txt (audit B-3 / C9).
Install-AccelerationRuntimePackages -ResolvedRuntime $resolvedRuntime

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
import os
from pathlib import Path

from huggingface_hub import snapshot_download

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
    print(f"Verified {item['id']}: {len(downloaded)} file(s) at {snapshot_path}")
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
