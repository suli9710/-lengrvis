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

function Invoke-Pip {
    param([string[]]$Packages)
    if ($Packages.Count -eq 0) { return }
    & $Python -m pip install --upgrade @Packages
}

function Resolve-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

$repoRoot = Resolve-RepoRoot
$backendPath = Join-Path $repoRoot "backend"
if (-not $ModelsDir) {
    $ModelsDir = Join-Path $repoRoot ".lengrvis_data\models"
}
$manifestPath = Join-Path $repoRoot "backend\app\acceleration\model_manifest.json"

Write-Step "Preparing Python packages"
Invoke-Pip @("pip", "wheel", "setuptools")
Invoke-Pip @("tokenizers>=0.20", "transformers>=4.45", "huggingface_hub>=0.24", "numpy>=1.26", "Pillow>=10.0")

if ($Runtime -in @("auto", "winml")) {
    Invoke-Pip @("onnxruntime-windowsml>=1.22", "onnxruntime-genai-winml>=0.8")
}
if ($Runtime -in @("auto", "directml")) {
    Invoke-Pip @("onnxruntime-directml>=1.18", "onnxruntime-genai-directml>=0.8")
}
if ($Runtime -in @("auto", "openvino")) {
    Invoke-Pip @("onnxruntime-openvino>=1.18", "openvino>=2024.4")
}
if ($Runtime -eq "cpu") {
    Invoke-Pip @("onnxruntime>=1.18", "onnxruntime-genai>=0.8")
}

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
    target = models_dir / item["path"]
    patterns = item.get("patterns") or None
    print(f"Downloading {item['id']} from {repo} -> {target}")
    snapshot_download(
        repo_id=repo,
        local_dir=str(target),
        allow_patterns=patterns,
        local_dir_use_symlinks=False,
        resume_download=True,
    )
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
