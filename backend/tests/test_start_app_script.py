from __future__ import annotations

from pathlib import Path


def _start_app_text(project_root: Path) -> str:
    return (project_root / "scripts" / "start_app.ps1").read_text(encoding="utf-8")


def test_start_app_defaults_lengrvis_env_once(project_root: Path) -> None:
    text = _start_app_text(project_root)
    assignment_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("$env:LENGRVIS_ENV =")
    ]

    assert "elseif ($env:LENGRVIS_ENV)" not in text
    assert assignment_lines == ['$env:LENGRVIS_ENV = "development"']


def test_start_app_installs_only_when_explicitly_requested(project_root: Path) -> None:
    text = _start_app_text(project_root)
    node_install_command = "& $Npm --prefix $DesktopDir install"
    python_install_command = '& $Python -m pip install -r (Join-Path $Root "backend\\requirements.txt")'
    node_guard = text.index("function Ensure-NodeDependencies")
    node_install = text.index(node_install_command, node_guard)
    python_guard = text.index("function Ensure-PythonDependencies")
    python_install = text.index(python_install_command, python_guard)

    assert text.count(node_install_command) == 1
    assert text.count(python_install_command) == 1
    assert text.index("if (-not $InstallMissingDependencies)", node_guard) < node_install
    assert text.index("if (-not $InstallMissingDependencies)", python_guard) < python_install
