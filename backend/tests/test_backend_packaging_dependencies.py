from __future__ import annotations

import re
from pathlib import Path


def _text(project_root: Path, relative_path: str) -> str:
    return (project_root / relative_path).read_text(encoding="utf-8")


def test_backend_build_requirements_pin_pyinstaller(project_root: Path) -> None:
    text = _text(project_root, "backend/requirements-build.txt")
    requirement_lines = [
        line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(requirement_lines) == 1
    assert re.fullmatch(r"pyinstaller==\d+\.\d+\.\d+", requirement_lines[0])


def test_backend_packaging_scripts_install_hashed_build_lock(project_root: Path) -> None:
    scripts = {
        "scripts/build_backend.ps1": _text(project_root, "scripts/build_backend.ps1"),
        "scripts/build_backend_mac.sh": _text(project_root, "scripts/build_backend_mac.sh"),
    }

    for path, text in scripts.items():
        assert "requirements-build-lock.txt" in text
        assert re.search(r"pip\s+install\s+--require-hashes\s+-r", text), path
        assert not re.search(r"pip\s+install\s+pyinstaller\b", text, flags=re.IGNORECASE), path
        assert "pip show pyinstaller" not in text.lower()
        assert "Failed to install hashed backend build dependencies" in text


def test_default_windows_packaging_gate_stays_under_500mb_without_bundled_ollama(
    project_root: Path,
) -> None:
    text = _text(project_root, "scripts/verify_packaging.ps1")

    assert "$MaximumDefaultReleaseArtifactBytes = 500MB" in text
    assert 'Test-MaximumFileSize "portable zip" $PortableZipPath $MaximumDefaultReleaseArtifactBytes' in text
    assert (
        'Test-MaximumFileSize "self-extracting executable" $SelfExtractingPath $MaximumDefaultReleaseArtifactBytes'
    ) in text

    default_branch = re.search(
        r"if \(\$RequireBundledOllama\) \{[\s\S]+?\}\s+else \{(?P<body>[\s\S]+?)\}\s+"
        r"Test-ReleaseSourceMapFreeDirectory",
        text,
    )
    assert default_branch is not None
    default_body = default_branch.group("body")
    assert 'Test-PathAbsent "portable Ollama runtime" $PortableOllamaDir' in default_body
    assert 'Test-PathAbsent "portable Ollama models" $PortableOllamaModelsDir' in default_body
    assert 'Test-PathAbsent "portable Ollama bundle manifest" $PortableOllamaManifest' in default_body

    zip_default_branch = re.search(
        r"if \(\$RequireBundledOllama\) \{[\s\S]+?Test-ZipDirectoryEntry \$Zip "
        r'"resources/ollama-models"[\s\S]+?\}\s+else \{(?P<body>[\s\S]+?)\}\s+'
        r"\}\s+finally",
        text,
    )
    assert zip_default_branch is not None
    zip_default_body = zip_default_branch.group("body")
    assert 'Test-ZipEntryAbsent $Zip "resources/ollama-bundle-manifest.json"' in zip_default_body
    assert 'Test-ZipDirectoryAbsent $Zip "resources/ollama"' in zip_default_body
    assert 'Test-ZipDirectoryAbsent $Zip "resources/ollama-models"' in zip_default_body

    assert "Default builds must download local models on demand" in text
    assert "separate offline Ollama/model package" in text
