from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _start_app_text(project_root: Path) -> str:
    return (project_root / "scripts" / "start_app.ps1").read_text(encoding="utf-8")


def _setup_dev_text(project_root: Path) -> str:
    return (project_root / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")


def _portable_first_screen_smoke_text(project_root: Path) -> str:
    return (project_root / "scripts" / "portable_first_screen_smoke.ps1").read_text(encoding="utf-8")


def _readme_text(project_root: Path) -> str:
    return (project_root / "README.md").read_text(encoding="utf-8")


def _release_gate_text(project_root: Path) -> str:
    return (project_root / "docs" / "qa" / "release-gate.md").read_text(encoding="utf-8")


def _e2e_acceptance_matrix_text(project_root: Path) -> str:
    return (project_root / "docs" / "qa" / "e2e-acceptance-matrix.md").read_text(encoding="utf-8")


def _productization_issues_text(project_root: Path) -> str:
    return (project_root / "PRODUCTIZATION_ISSUES.md").read_text(encoding="utf-8")


def _agentic_product_evals_text(project_root: Path) -> str:
    return (project_root / "docs" / "qa" / "agentic-product-evals.md").read_text(encoding="utf-8")


def _parity_text(project_root: Path) -> str:
    return (project_root / "docs" / "LENGRVIS_PARITY.md").read_text(encoding="utf-8")


def _launcher_cmd_text(project_root: Path) -> str:
    return (project_root / "Start-Lengrvis.cmd").read_text(encoding="utf-8")


def _debug_cmd_text(project_root: Path) -> str:
    return (project_root / "Start-Lengrvis-Debug.cmd").read_text(encoding="utf-8")


def _markdown_section(text: str, heading: str) -> str:
    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    return text[start:] if next_heading == -1 else text[start:next_heading]


def test_start_app_defaults_lengrvis_env_once(project_root: Path) -> None:
    text = _start_app_text(project_root)
    assignment_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("$env:LENGRVIS_ENV =")
    ]

    assert "elseif ($env:LENGRVIS_ENV)" not in text
    assert assignment_lines == ['$env:LENGRVIS_ENV = "development"']


def test_start_app_never_installs_dependencies(project_root: Path) -> None:
    text = _start_app_text(project_root)

    assert "& $Npm --prefix $DesktopDir install" not in text
    assert "& $npm --prefix $DesktopDir install" not in text
    assert "& $Python -m pip install" not in text
    assert "& $python -m pip install" not in text
    assert "正式启动不会现场运行" in text


def test_setup_dev_owns_dependency_install(project_root: Path) -> None:
    text = _setup_dev_text(project_root)

    assert "& $python -m pip install -U pip" in text
    assert "& $python -m pip install -r $requirementsPath" in text
    assert "& $npm --prefix $DesktopDir ci" in text
    assert "& $npm --prefix $DesktopDir install" in text


def test_start_app_does_not_stop_workspace_owned_full_backend(project_root: Path) -> None:
    text = _start_app_text(project_root)
    function_start = text.index("function Stop-FullBackendIfWorkspaceOwned")
    function_end = text.index("function Stop-WorkspaceProcessOnPort", function_start)
    function_body = text[function_start:function_end]

    assert "Stop-Process" not in function_body
    assert "Stop-VerifiedListenProcess" not in function_body
    assert "为避免误关用户手动启动的服务" in function_body
    assert ".Contains(\"backend.main:full_app\")" not in function_body


def test_start_app_main_backend_reuses_or_blocks_existing_listener(project_root: Path) -> None:
    text = _start_app_text(project_root)
    function_start = text.index("function Start-Backend")
    function_end = text.index("function Start-DesktopShell", function_start)
    function_body = text[function_start:function_end]

    assert "elseif ((Test-WorkspaceProcess $commandLine) -or (Test-UvicornLengrvisBackend $commandLine))" not in function_body
    assert "Stop-VerifiedListenProcess -Port $BackendPort -Process $existing" not in function_body
    assert "if (Test-Health)" in function_body
    assert "为避免误关用户手动启动的服务" in function_body


def test_start_app_does_not_stop_port_discovered_processes(project_root: Path) -> None:
    text = _start_app_text(project_root)
    helper_start = text.index("function Stop-VerifiedListenProcess")
    helper_end = text.index("function Test-PackagedLengrvisBackend", helper_start)
    helper_body = text[helper_start:helper_end]

    assert "$current = Get-ListenProcess $Port" in helper_body
    assert "$currentPid -ne $processId" in helper_body
    assert "Stop-Process -Id $processId" in helper_body

    for function_name, next_function_name in [
        ("Stop-FullBackendIfWorkspaceOwned", "Stop-WorkspaceProcessOnPort"),
        ("Stop-WorkspaceProcessOnPort", "Stop-WorkspaceListenerOnPort"),
        ("Stop-WorkspaceListenerOnPort", "Ensure-NodeDependencies"),
    ]:
        function_start = text.index(f"function {function_name}")
        function_end = text.index(f"function {next_function_name}", function_start)
        function_body = text[function_start:function_end]
        assert "Stop-VerifiedListenProcess" not in function_body
        assert "Stop-Process -Id $" not in function_body

    backend_start = text.index("function Start-Backend")
    backend_end = text.index("function Start-Frontend", backend_start)
    backend_body = text[backend_start:backend_end]
    assert "Stop-VerifiedListenProcess -Port $BackendPort -Process $existing" not in backend_body
    assert "Stop-Process -Id $existing.ProcessId" not in backend_body

    frontend_start = text.index("function Start-Frontend")
    frontend_end = text.index("function Get-RunningDesktopProcess", frontend_start)
    frontend_body = text[frontend_start:frontend_end]
    assert "Stop-VerifiedListenProcess -Port $FrontendPort -Process $existing" not in frontend_body
    assert "Stop-Process -Id $existing.ProcessId" not in frontend_body


def test_start_app_frontend_reuses_only_lengrvis_frontend_listener(project_root: Path) -> None:
    text = _start_app_text(project_root)
    helper_start = text.index("function Test-LengrvisFrontendProcess")
    helper_end = text.index("function Stop-FullBackendIfWorkspaceOwned", helper_start)
    helper_body = text[helper_start:helper_end]

    assert "Test-WorkspaceProcess $CommandLine" in helper_body
    assert "\\desktop\\node_modules\\" in helper_body
    assert "vite" in helper_body

    frontend_start = text.index("function Start-Frontend")
    frontend_end = text.index("function Get-RunningDesktopProcess", frontend_start)
    frontend_body = text[frontend_start:frontend_end]

    assert "if (Test-LengrvisFrontendProcess $commandLine)" in frontend_body
    assert frontend_body.index("if (Test-LengrvisFrontendProcess $commandLine)") < frontend_body.index("Invoke-WebRequest -Uri $FrontendUrl")
    assert "界面服务端口 $FrontendPort 已被占用，但无法复用" in frontend_body


def test_debug_launcher_prints_redacted_summary_not_raw_logs(project_root: Path) -> None:
    text = _debug_cmd_text(project_root)

    assert "-PrintRecentLogs" in text
    assert "\ntype " not in text.lower()


def test_user_launch_docs_point_to_settings_and_debug_not_env_config(project_root: Path) -> None:
    text = _readme_text(project_root)
    quick_start = _markdown_section(text, "## 普通用户快速开始")
    user_entry = _markdown_section(text, "## 普通用户配置与诊断入口")

    assert ".env" not in quick_start
    assert "config.yaml" not in quick_start
    assert "桌面窗口里的“设置”" in user_entry
    assert "普通用户不需要手动编辑 `.env` 或 `config.yaml`" in user_entry
    assert "Start-Lengrvis-Debug.cmd" in user_entry
    assert "导出诊断包" in user_entry
    assert "开发者可选真实 AI 配置" in text


def test_launchers_warn_non_developers_not_to_edit_env_or_config(project_root: Path) -> None:
    launcher_text = _launcher_cmd_text(project_root)
    debug_text = _debug_cmd_text(project_root)
    start_app_text = _start_app_text(project_root)

    assert "不要自行编辑 .env 或 config.yaml" in launcher_text
    assert "不要自行编辑 .env 或 config.yaml" in debug_text
    assert "普通用户不要自行编辑 .env 或 config.yaml" in start_app_text
    assert "Start-Lengrvis-Debug.cmd 查看最近错误" in launcher_text
    assert "-PrintRecentLogs" in debug_text
    assert "Write-NextStep $failureMessage" in start_app_text


def test_desktop_copy_exposes_settings_and_diagnostics_as_user_entrypoints(project_root: Path) -> None:
    settings_text = (project_root / "desktop" / "src" / "renderer" / "components" / "SettingsPanel.tsx").read_text(encoding="utf-8")
    system_info_text = (project_root / "desktop" / "src" / "renderer" / "components" / "SystemInfoPanel.tsx").read_text(encoding="utf-8")

    assert "统一配置入口" in settings_text
    assert "不需要手动编辑 .env 或 config.yaml" in settings_text
    assert "导出诊断包" in system_info_text
    assert "不需要打开配置文件" in system_info_text


def test_portable_first_screen_smoke_proves_read_only_diagnostics(project_root: Path) -> None:
    text = _portable_first_screen_smoke_text(project_root)

    assert '$diagnosticsUrl = "$backendUrl/api/system/diagnostics"' in text
    assert "function Test-ReadOnlyDiagnostics" in text
    assert "X-Lengrvis-Desktop-Token" in text
    assert "LENGRVIS_DESKTOP_API_TOKEN = $desktopApiToken" in text
    assert "Invoke-WebRequest -Uri $DiagnosticsUrl -UseBasicParsing -TimeoutSec 5 -Method Get -Headers $headers" in text
    assert 'diagnosticScope -ne "local_only"' in text
    assert "local_paths.data_dir did not use the smoke temp data dir" in text
    assert "local_paths.database was outside the smoke temp data dir" in text
    assert "product_metrics.schema_version was missing" in text
    assert "Redact-SmokeText $Message" in text
    assert "-DesktopApiToken $desktopApiToken" in text
    assert "$diagnosticsObserved = $false" in text
    assert "if ($diagnosticsProbe.Ok -and $windowObserved)" in text
    assert "backend read-only diagnostics passed; waiting for portable window handle" in text
    assert "read-only diagnostics passed; no portable window handle yet" in text
    assert "did not prove both a visible portable window and backend diagnostics" in text
    assert "window pid=$windowProcessId" in text
    assert 'passReason = "backend answered $healthUrl"' not in text
    assert 'passReason = "window appeared' not in text


def test_portable_first_screen_smoke_attempts_renderer_dom_read_only_task_evidence(project_root: Path) -> None:
    text = _portable_first_screen_smoke_text(project_root)

    assert "$remoteDebuggingPort = Get-FreeTcpPort" in text
    assert "--remote-debugging-address=127.0.0.1" in text
    assert "--remote-debugging-port=$remoteDebuggingPort" in text
    assert "[switch]$AllowBackendOnlyPass" in text
    assert "function Exit-SmokeUnsupported" in text
    assert "function Test-RendererDomEvidence" in text
    assert "function Invoke-PortableRendererDomAutomation" in text
    assert "chromium.connectOverCDP" in text
    assert "page.locator(\"button\").filter({ hasText: systemCheckPattern })" in text
    assert "portable renderer DOM read-only task evidence passed" in text
    assert "launcher/window/backend diagnostics pass remains limited" in text
    assert "renderer DOM evidence unavailable in strict portable smoke" in text
    assert "rerun with -AllowBackendOnlyPass only for legacy launcher/window/backend diagnostics evidence" in text
    assert "renderer DOM evidence status: $rendererEvidenceStatus" in text


def test_portable_first_screen_smoke_forbids_renderer_export_and_write_side_effects(project_root: Path) -> None:
    text = _portable_first_screen_smoke_text(project_root)

    assert "const allowedReadOnlyGetEndpoints = new Set([" in text
    assert '"/health"' in text
    assert '"/api/health"' in text
    assert '"/api/tasks"' in text
    assert '"/api/settings/llm/health"' in text
    assert '"/api/settings/llm/cost-summary"' in text
    assert '"/api/system/info"' in text
    assert '"/api/system/diagnostics"' in text
    assert '"/api/system/processes"' in text
    assert '"/api/system/startup-items"' in text
    assert '"/api/apps"' in text
    assert 'function isApiEndpoint(endpoint)' in text
    assert "function isDisallowedReadOnlyApiCall(call)" in text
    assert 'if (method !== "GET") return true;' in text
    assert "return !allowedReadOnlyGetEndpoints.has(endpoint);" in text
    assert "let observeReadOnlyClick = false;" in text
    assert "if (!observeReadOnlyClick) return;" in text
    assert "window.__portableSmokeBridgeCalls.length = 0" in text
    assert "observeReadOnlyClick = true;" in text
    assert 'wrap("system.exportDiagnosticsPackage"' in text
    assert 'wrap("runs.start"' in text
    assert "read-only GUI evidence cannot rely on web fallback requests" in text
    assert "natural-language GUI evidence cannot rely on web fallback state" in text
    assert "read-only renderer API call outside allowlist after system-check click" in text
    assert "system-check click did not invoke /api/system/diagnostics through the packaged renderer" in text
    assert "observed packaged renderer /api/system/diagnostics plus read-only diagnostics copy" in text
    assert "lengrvis-api-request" in text
    assert 'const forbiddenWritePrefixes = ["/api/runs", "/api/chat", "/api/tasks"];' not in text
    assert "function Test-NoPortableWriteSideEffects" in text
    assert 'Url = "$BackendUrl/api/tasks"' in text
    assert 'Url = "$BackendUrl/api/runs"' in text
    assert 'Url = "$BackendUrl/api/chat/messages"' in text
    assert 'Join-Path $ExpectedDataDir "diagnostic-packages"' in text
    assert "no chat/run/task writes and no diagnostics export package after GUI click" in text
    assert 'Invoke-WebRequest -Uri "$backendUrl/api/system/diagnostics/export"' not in text


def test_portable_first_screen_smoke_attempts_natural_language_read_only_task_evidence(project_root: Path) -> None:
    text = _portable_first_screen_smoke_text(project_root)

    assert "function Invoke-PortableNaturalLanguageDomAutomation" in text
    assert 'const naturalLanguagePrompt = "\\u5e2e\\u6211\\u68c0\\u67e5\\u8fd9\\u53f0\\u7535\\u8111";' in text
    assert "function Test-PortableNaturalLanguageTaskEvidence" in text
    assert ".office-command-dock textarea" in text
    assert 'const expectedPostEndpoints = new Set(["/api/chat", "/api/runs"]);' in text
    assert "function waitForRendererBackendConnection(page, deadline)" in text
    assert "window.lengrvis?.backend?.getStatus" in text
    assert 'endpoint: "/api/health"' in text
    assert 'const refreshButtonSelector = \'button[aria-label="\\\\u5237\\\\u65b0"]' in text
    assert "function waitForCommandDockReady(page, input, deadline)" in text
    assert "packaged command dock send remained disabled after renderer/backend readiness wait" in text
    assert "backend task/run evidence will be verified separately" in text
    assert "lengrvis-api-request" in text
    assert "function Get-SmokeCollectionItems" in text
    assert "function Get-SmokeRecordIds" in text
    assert "BaselineTaskIds" in text
    assert "BaselineRunIds" in text
    assert "Write-Output -NoEnumerate $set" in text
    assert "$baselineTaskIdSet = New-SmokeStringSet -Values $BaselineTaskIds" in text
    assert "$baselineRunIdSet = New-SmokeStringSet -Values $BaselineRunIds" in text
    assert "$taskId -and -not $baselineTaskIdSet.Contains($taskId)" in text
    assert "$runId -and -not $baselineRunIdSet.Contains($runId)" in text
    assert "could not capture natural-language backend baseline before packaged prompt submission" in text
    assert "backend evidence observed after renderer bridge submission attempt, but no packaged /api/chat or /api/runs POST was observed; keeping natural-language evidence unsupported" in text
    assert "inferNaturalLanguagePostFromBackend" not in text
    assert "inferred: true" not in text
    assert "$messages.Count -gt 0 -or" not in text
    assert "natural-language command dock displayed clear visible safe failure before submit; no packaged task submission was possible" in text
    assert "visible safe failure is not accepted as natural-language task evidence" in text
    assert "natural-language visible safe failure side-effect check failed" in text
    assert "portable renderer DOM natural-language read-only task evidence passed" in text
    assert "natural-language renderer DOM evidence failed" in text
    assert "read-only entry evidence remains valid but must not be counted as natural-language task evidence" in text
    assert "natural-language prompt created read-only/system diagnostics task" in text
    assert "natural-language prompt produced clear visible safe failure copy in the packaged command dock, but no /api/chat or /api/runs POST was observed" in text
    assert "safeFailureTask=$taskId status=$taskStatus" in text
    assert "safeFailureRun=$runId phase=$runPhase" in text
    assert "safeFailureChatWithoutTaskOrRun=true" in text
    assert "natural-language prompt did not expose concrete read-only/system diagnostics task or run evidence" in text
    assert "$highRiskPattern" in text
    assert "trash|rollback|uninstall" not in text
    assert "delete|remove" not in text
    assert "natural-language result proven by visible safe failure copy" not in text
    assert "natural-language prompt returned clear safe failure copy without creating task/run records" not in text
    assert 'Get-SmokeJson -Url "$BackendUrl/api/runs/$runId/timeline"' in text
    assert 'Get-SmokeJson -Url "$BackendUrl/api/runs/$runId/progress"' in text
    assert r"/^\/api\/settings" in text
    assert r"/^\/api\/files" in text
    assert r"/^\/api\/apps" in text


def test_portable_docs_do_not_overclaim_gui_task_automation(project_root: Path) -> None:
    release_gate = _release_gate_text(project_root)
    parity = _parity_text(project_root)

    assert "Only the explicit renderer DOM evidence line counts as packaged GUI-task automation" in release_gate
    assert "Any POST/PUT/PATCH/DELETE, unknown API mutation, diagnostics export, or settings/files/apps mutation during the read-only click fails the smoke" in release_gate
    assert "that pass requires a packaged renderer `/api/chat` or `/api/runs` POST plus backend read-only/system diagnostics task or run evidence" in release_gate
    assert "Visible safe-failure copy is still useful safety evidence when paired with zero side effects, but it is not accepted as natural-language task evidence" in release_gate
    assert "This is submission/task-evidence coverage, not release-candidate completion sign-off" in release_gate
    assert "observes `/api/chat` or `/api/runs` and a related task/run" in release_gate
    assert "If CDP or the packaged renderer cannot be automated, the strict script exits 2 with `[unsupported]`" in release_gate
    assert "packaged renderer DOM automation to click the read-only" in parity
    assert "observed packaged renderer `POST /api/runs`" in parity
    assert "Record this as packaged natural-language command-dock submission plus read-only/system diagnostics task evidence" in parity
    assert "does not prove clean-machine release-candidate install" in parity
    assert "full natural-language agent task completion loop" in parity
    assert "separate manual release evidence" in parity


def test_portable_docs_reference_latest_natural_language_evidence(project_root: Path) -> None:
    release_gate = _release_gate_text(project_root)
    matrix = _e2e_acceptance_matrix_text(project_root)
    productization = _productization_issues_text(project_root)
    agentic_evals = _agentic_product_evals_text(project_root)
    combined = "\n".join([release_gate, matrix, productization, agentic_evals])

    latest_run = r".tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259"
    stale_runs = [
        "run-20260608-141325-18256-1520d784",
        "run-20260608-123849-34760-bc8d1829",
    ]

    for text in (release_gate, matrix, productization, agentic_evals):
        assert latest_run in text
        assert "POST /api/runs" in text
        assert "read-only/system diagnostics task evidence" in text

    for stale_run in stale_runs:
        assert stale_run not in combined

    assert "send stayed disabled" not in combined
    assert "visible safe-failure plus zero-write safety evidence" not in combined


def test_start_app_recent_log_summary_redacts_secrets(project_root: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "backend.65535.redaction-test.err.log"
    raw_secrets = [
        "sk-test-startup-1234567890abcdef",
        "bearer-startup-secret-1234567890",
        "cookie-startup-secret-1234567890",
        "url-startup-token-1234567890",
        "startup-oauth-code-1234567890",
        "startup-client-secret-1234567890",
        "startup-desktop-token-1234567890",
    ]
    log_path.write_text(
        "\n".join(
            [
                f"api_key={raw_secrets[0]}",
                f"Authorization: Bearer {raw_secrets[1]}",
                f"Cookie: session={raw_secrets[2]}",
                "callback=https://example.test/oauth"
                f"?token={raw_secrets[3]}&code={raw_secrets[4]}&client_secret={raw_secrets[5]}",
                f"X-Lengrvis-Desktop-Token={raw_secrets[6]}",
            ]
        ),
        encoding="utf-8",
    )

    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(project_root / "scripts" / "start_app.ps1"),
                "-PrintRecentLogs",
            ],
            cwd=project_root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=20,
        )
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        for secret in raw_secrets:
            assert secret not in output
        assert "[redacted]" in output
    finally:
        log_path.unlink(missing_ok=True)
