param(
    [string]$PortableDir = "dist\Lengrvis-win-portable",
    [ValidateRange(5, 300)]
    [int]$TimeoutSeconds = 60,
    [string]$Workspace = ".tmp\portable-first-screen-smoke",
    [switch]$AllowBackendOnlyPass,
    [switch]$RemoveTempOnPass
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$script:SmokeStatusLog = $null

function Resolve-SmokePath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Redact-SmokeText {
    param([AllowNull()][object]$Value)

    $text = [string]$Value
    if ([string]::IsNullOrEmpty($text)) {
        return $text
    }

    $sensitiveKeyPattern = "x-lengrvis-desktop-token|authorization|cookie|set-cookie|api[_-]?key|apikey|desktop[_-]?token|access[_-]?token|refresh[_-]?token|auth[_-]?token|oauth[_-]?token|client[_-]?secret|token|secret|password|passwd|pwd|jwt|session(?:[_-]?id)?|code"
    $redacted = [regex]::Replace($text, "(?i)\b($sensitiveKeyPattern)\b(\s*[:=]\s*)(?:Bearer\s+)?[^;,\s\)]+", '$1$2[redacted]')
    $redacted = [regex]::Replace($redacted, "(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [redacted]")
    $redacted = [regex]::Replace($redacted, "(?i)([?&](?:$sensitiveKeyPattern)=)[^&#\s]+", '$1[redacted]')
    $redacted = [regex]::Replace($redacted, "sk-(?:proj-)?[A-Za-z0-9_-]{8,}", "sk-[redacted]")
    return $redacted
}

function Write-SmokeStatus {
    param(
        [string]$Status,
        [string]$Message,
        [ConsoleColor]$Color = [ConsoleColor]::White
    )

    $line = "[$Status] $(Redact-SmokeText $Message)"
    Write-Host $line -ForegroundColor $Color
    if ($script:SmokeStatusLog) {
        Add-Content -LiteralPath $script:SmokeStatusLog -Value $line -Encoding UTF8
    }
}

function Exit-SmokeBlocked {
    param([string]$Message)

    Write-SmokeStatus "blocked" $Message ([ConsoleColor]::Yellow)
    exit 2
}

function Exit-SmokeFailed {
    param([string]$Message)

    Write-SmokeStatus "fail" $Message ([ConsoleColor]::Red)
    exit 1
}

function Exit-SmokeUnsupported {
    param([string]$Message)

    Write-SmokeStatus "unsupported" $Message ([ConsoleColor]::Yellow)
    exit 2
}

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), 0)
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Convert-ToSmokeComparablePath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }

    $trimChars = [char[]]@("\", "/")
    try {
        return [System.IO.Path]::GetFullPath($Path).TrimEnd($trimChars).ToLowerInvariant()
    }
    catch {
        return $Path.TrimEnd($trimChars).ToLowerInvariant()
    }
}

function Test-SmokePathUnder {
    param(
        [string]$Path,
        [string]$RootPath
    )

    $candidate = Convert-ToSmokeComparablePath $Path
    $rootPathComparable = Convert-ToSmokeComparablePath $RootPath
    if (-not $candidate -or -not $rootPathComparable) {
        return $false
    }
    return $candidate -eq $rootPathComparable -or $candidate.StartsWith("$rootPathComparable\", [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ExecutableProcessIds {
    param([string]$ExecutablePath)

    if ([string]::IsNullOrWhiteSpace($ExecutablePath) -or -not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
        return @()
    }

    $fullPath = (Resolve-Path -LiteralPath $ExecutablePath).Path
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.Equals($fullPath, [System.StringComparison]::OrdinalIgnoreCase)
        } | ForEach-Object { [int]$_.ProcessId } | Select-Object -Unique)
    }
    catch {
        return @()
    }
}

function Get-SmokeProcessTreeIds {
    param([int]$RootProcessId)

    try {
        $processRows = @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId, ParentProcessId)
    }
    catch {
        return @()
    }

    $childrenByParent = @{}
    foreach ($processRow in $processRows) {
        $parentId = [int]$processRow.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parentId)) {
            $childrenByParent[$parentId] = New-Object System.Collections.Generic.List[int]
        }
        $childrenByParent[$parentId].Add([int]$processRow.ProcessId)
    }

    $processIds = New-Object System.Collections.Generic.List[int]
    function Add-ChildProcessIds {
        param([int]$CurrentProcessId)

        if (-not $childrenByParent.ContainsKey($CurrentProcessId)) {
            return
        }
        foreach ($childProcessId in $childrenByParent[$CurrentProcessId]) {
            Add-ChildProcessIds -CurrentProcessId $childProcessId
            $processIds.Add($childProcessId)
        }
    }

    Add-ChildProcessIds -CurrentProcessId $RootProcessId
    return @($processIds | Select-Object -Unique)
}

function Select-NewProcessIds {
    param(
        [int[]]$CurrentProcessIds,
        [int[]]$BaselineProcessIds
    )

    $baseline = New-Object System.Collections.Generic.HashSet[int]
    foreach ($processId in $BaselineProcessIds) {
        [void]$baseline.Add([int]$processId)
    }
    return @($CurrentProcessIds | Where-Object { -not $baseline.Contains([int]$_) } | Select-Object -Unique)
}

function Get-LaunchedProcessIds {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$LauncherPath,
        [string]$BackendPath,
        [int[]]$BaselineLauncherIds,
        [int[]]$BaselineBackendIds
    )

    $ids = New-Object System.Collections.Generic.List[int]
    if ($Process) {
        $ids.Add([int]$Process.Id)
        foreach ($childProcessId in @(Get-SmokeProcessTreeIds -RootProcessId $Process.Id)) {
            $ids.Add([int]$childProcessId)
        }
    }

    foreach ($processId in @(Select-NewProcessIds -CurrentProcessIds (Get-ExecutableProcessIds -ExecutablePath $LauncherPath) -BaselineProcessIds $BaselineLauncherIds)) {
        $ids.Add([int]$processId)
    }
    foreach ($processId in @(Select-NewProcessIds -CurrentProcessIds (Get-ExecutableProcessIds -ExecutablePath $BackendPath) -BaselineProcessIds $BaselineBackendIds)) {
        $ids.Add([int]$processId)
    }

    return @($ids | Select-Object -Unique)
}

function Get-CurrentProcessFamilyIds {
    $ids = New-Object System.Collections.Generic.HashSet[int]
    $currentId = [int]$PID
    while ($currentId -gt 0 -and -not $ids.Contains($currentId)) {
        [void]$ids.Add($currentId)
        try {
            $row = Get-CimInstance Win32_Process -Filter "ProcessId=$currentId" -ErrorAction Stop
            if (-not $row -or -not $row.ParentProcessId) {
                break
            }
            $currentId = [int]$row.ParentProcessId
        }
        catch {
            break
        }
    }
    return $ids
}

function Test-BackendHealth {
    param([string]$HealthUrl)

    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

function Test-ReadOnlyDiagnostics {
    param(
        [string]$DiagnosticsUrl,
        [string]$ExpectedDataDir,
        [string]$DesktopApiToken
    )

    try {
        $headers = @{}
        if (-not [string]::IsNullOrWhiteSpace($DesktopApiToken)) {
            $headers["X-Lengrvis-Desktop-Token"] = $DesktopApiToken
        }
        $response = Invoke-WebRequest -Uri $DiagnosticsUrl -UseBasicParsing -TimeoutSec 5 -Method Get -Headers $headers
        $statusCode = [int]$response.StatusCode
        if ($statusCode -lt 200 -or $statusCode -ge 300) {
            return [pscustomobject]@{
                Ok = $false
                Message = "read-only diagnostics GET returned HTTP $statusCode"
            }
        }

        try {
            $payload = $response.Content | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            return [pscustomobject]@{
                Ok = $false
                Message = "read-only diagnostics GET returned non-JSON content: $(Redact-SmokeText $_.Exception.Message)"
            }
        }

        $errors = New-Object System.Collections.Generic.List[string]
        $productName = [string]$payload.product.name
        $diagnosticScope = [string]$payload.diagnostic_scope
        $payloadDataDir = [string]$payload.local_paths.data_dir
        $payloadDatabase = [string]$payload.local_paths.database

        if ($productName -ne "Lengrvis") {
            $errors.Add("product.name was '$productName'")
        }
        if ($diagnosticScope -ne "local_only") {
            $errors.Add("diagnostic_scope was '$diagnosticScope'")
        }
        if (-not $payloadDataDir) {
            $errors.Add("local_paths.data_dir was missing")
        }
        elseif ((Convert-ToSmokeComparablePath $payloadDataDir) -ne (Convert-ToSmokeComparablePath $ExpectedDataDir)) {
            $errors.Add("local_paths.data_dir did not use the smoke temp data dir")
        }
        if (-not $payloadDatabase) {
            $errors.Add("local_paths.database was missing")
        }
        elseif (-not (Test-SmokePathUnder -Path $payloadDatabase -RootPath $ExpectedDataDir)) {
            $errors.Add("local_paths.database was outside the smoke temp data dir")
        }
        if (-not $payload.product_metrics -or [int]($payload.product_metrics.schema_version) -lt 1) {
            $errors.Add("product_metrics.schema_version was missing")
        }
        if (-not $payload.diagnostic_hints -or @($payload.diagnostic_hints).Count -lt 1) {
            $errors.Add("diagnostic_hints was empty")
        }

        if ($errors.Count -gt 0) {
            return [pscustomobject]@{
                Ok = $false
                Message = "read-only diagnostics GET schema check failed: $($errors -join '; ')"
            }
        }

        return [pscustomobject]@{
            Ok = $true
            Message = "read-only diagnostics GET succeeded with scope=$diagnosticScope, product=$productName, temp data dir confirmed; no export/write endpoint invoked"
        }
    }
    catch {
        $statusCode = $null
        try {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }
        }
        catch {
            $statusCode = $null
        }

        $statusText = if ($statusCode) { "HTTP $statusCode" } else { "request error" }
        return [pscustomobject]@{
            Ok = $false
            Message = "read-only diagnostics GET failed ($statusText): $(Redact-SmokeText $_.Exception.Message)"
        }
    }
}

function Get-SmokeJson {
    param(
        [string]$Url,
        [string]$DesktopApiToken
    )

    $headers = @{}
    if (-not [string]::IsNullOrWhiteSpace($DesktopApiToken)) {
        $headers["X-Lengrvis-Desktop-Token"] = $DesktopApiToken
    }

    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -Method Get -Headers $headers
    $statusCode = [int]$response.StatusCode
    if ($statusCode -lt 200 -or $statusCode -ge 300) {
        throw "GET $Url returned HTTP $statusCode"
    }
    return $response.Content | ConvertFrom-Json -ErrorAction Stop
}

function Get-SmokeCollectionCount {
    param(
        [AllowNull()][object]$Payload,
        [string[]]$Keys
    )

    if ($null -eq $Payload) {
        return 0
    }
    if ($Payload -is [array]) {
        return @($Payload).Count
    }
    foreach ($key in $Keys) {
        if ($Payload.PSObject.Properties.Name -contains $key) {
            $value = $Payload.$key
            if ($null -eq $value) {
                return 0
            }
            return @($value).Count
        }
    }
    return 0
}

function Test-NoPortableWriteSideEffects {
    param(
        [string]$BackendUrl,
        [string]$ExpectedDataDir,
        [string]$DesktopApiToken
    )

    $errors = New-Object System.Collections.Generic.List[string]
    $observations = New-Object System.Collections.Generic.List[string]
    $checks = @(
        @{ Name = "tasks"; Url = "$BackendUrl/api/tasks"; Keys = @("tasks", "items", "results") },
        @{ Name = "runs"; Url = "$BackendUrl/api/runs"; Keys = @("runs", "items", "results") },
        @{ Name = "chat messages"; Url = "$BackendUrl/api/chat/messages"; Keys = @("messages", "items", "results") }
    )

    foreach ($check in $checks) {
        try {
            $payload = Get-SmokeJson -Url $check.Url -DesktopApiToken $DesktopApiToken
            $count = Get-SmokeCollectionCount -Payload $payload -Keys $check.Keys
            $observations.Add("$($check.Name)=$count")
            if ($count -ne 0) {
                $errors.Add("$($check.Name) count was $count")
            }
        }
        catch {
            return [pscustomobject]@{
                Ok = $false
                Status = "unsupported"
                Message = "could not verify absence of write-side effects at $($check.Url): $(Redact-SmokeText $_.Exception.Message)"
            }
        }
    }

    $exportDir = Join-Path $ExpectedDataDir "diagnostic-packages"
    $exportFiles = @()
    if (Test-Path -LiteralPath $exportDir -PathType Container) {
        $exportFiles = @(Get-ChildItem -LiteralPath $exportDir -File -ErrorAction SilentlyContinue)
    }
    $observations.Add("diagnostic-packages=$($exportFiles.Count)")
    if ($exportFiles.Count -ne 0) {
        $errors.Add("diagnostics export created $($exportFiles.Count) package file(s)")
    }

    if ($errors.Count -gt 0) {
        return [pscustomobject]@{
            Ok = $false
            Status = "fail"
            Message = "renderer click produced write-side effects: $($errors -join '; ')"
        }
    }

    return [pscustomobject]@{
        Ok = $true
        Status = "pass"
        Message = "no chat/run/task writes and no diagnostics export package after GUI click ($($observations -join '; '))"
    }
}

function Invoke-PortableRendererDomAutomation {
    param(
        [int]$RemoteDebuggingPort,
        [string]$RunRoot,
        [int]$TimeoutSeconds
    )

    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCommand) {
        return [pscustomobject]@{
            Ok = $false
            Status = "unsupported"
            Message = "Node.js is not available, so packaged renderer DOM automation could not run."
        }
    }

    $playwrightModule = Join-Path $Root "desktop\node_modules\playwright"
    if (-not (Test-Path -LiteralPath $playwrightModule -PathType Container)) {
        return [pscustomobject]@{
            Ok = $false
            Status = "unsupported"
            Message = "Playwright module missing at $playwrightModule; run desktop dependencies before renderer DOM automation."
        }
    }

    $automationScript = Join-Path $RunRoot "portable-renderer-dom-evidence.cjs"
    $automationLog = Join-Path $RunRoot "portable-renderer-dom-evidence.log"
    $automationSource = @'
const path = require("node:path");

const allowedReadOnlyGetEndpoints = new Set([
  "/health",
  "/api/health",
  "/api/tasks",
  "/api/settings/llm/health",
  "/api/settings/llm/cost-summary",
  "/api/system/info",
  "/api/system/diagnostics",
  "/api/system/processes",
  "/api/system/startup-items",
  "/api/apps"
]);
const systemCheckPattern = new RegExp([
  "\\u68c0\\u67e5\\u7535\\u8111\\u72b6\\u6001",
  "\\u7535\\u8111\\u72b6\\u6001",
  "\\u53ea\\u8bfb\\u68c0\\u67e5",
  "\\u7cfb\\u7edf\\u68c0\\u67e5",
  "\\u91cd\\u65b0\\u68c0\\u67e5",
  "\\u7acb\\u5373\\u53ea\\u8bfb\\u68c0\\u67e5"
].join("|"));
const readOnlySystemCopyPattern = new RegExp([
  "\\u7cfb\\u7edf\\u4fe1\\u606f",
  "\\u53ea\\u8bfb\\u8bca\\u65ad",
  "\\u53ea\\u8bfb\\u68c0\\u67e5",
  "\\u4e0d\\u6539\\u8bbe\\u7f6e",
  "\\u7535\\u8111\\u5065\\u5eb7",
  "Windows \\u6838\\u5fc3\\u80fd\\u529b"
].join("|"));

function finish(status, message, extra = {}) {
  const payload = { ok: status === "pass", status, message, ...extra };
  process.stdout.write(JSON.stringify(payload));
  process.exit(status === "pass" ? 0 : status === "fail" ? 1 : 2);
}

function remaining(deadline) {
  return Math.max(250, deadline - Date.now());
}

function compactText(text, limit = 420) {
  return String(text || "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function normalizeEndpoint(value) {
  if (!value) return "";
  try {
    return new URL(String(value), "http://127.0.0.1").pathname;
  } catch {
    return String(value);
  }
}

function isApiEndpoint(endpoint) {
  return endpoint === "/api" || endpoint.startsWith("/api/");
}

function isDisallowedReadOnlyApiCall(call) {
  const method = String(call.method || "GET").toUpperCase();
  const endpoint = normalizeEndpoint(call.endpoint);
  if (call.kind === "system.exportDiagnosticsPackage" || call.kind === "runs.start") return true;
  if (!isApiEndpoint(endpoint)) return false;
  if (method !== "GET") return true;
  return !allowedReadOnlyGetEndpoints.has(endpoint);
}

async function waitForRendererPage(browser, deadline) {
  while (Date.now() < deadline) {
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const url = page.url();
        if (!url.startsWith("devtools://")) {
          return page;
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return null;
}

async function instrumentBridge(page) {
  return page.evaluate(() => {
    const result = { hasBridge: Boolean(window.lengrvis), wrapped: [], errors: [] };
    const calls = [];
    try {
      Object.defineProperty(window, "__portableSmokeBridgeCalls", {
        value: calls,
        configurable: true
      });
    } catch {
      window.__portableSmokeBridgeCalls = calls;
    }

    function wrap(path, container, property, mapper) {
      if (!container || typeof container[property] !== "function") {
        result.errors.push(`${path} missing`);
        return;
      }
      const original = container[property];
      try {
        container[property] = function wrappedPortableSmokeBridgeCall(...args) {
          try {
            calls.push({ path, ...mapper(args) });
          } catch (error) {
            calls.push({ path, error: error instanceof Error ? error.message : String(error) });
          }
          return original.apply(this, args);
        };
        result.wrapped.push(path);
      } catch (error) {
        result.errors.push(`${path}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }

    try {
      window.addEventListener("lengrvis-api-request", (event) => {
        const detail = event?.detail || {};
        calls.push({
          path: "lengrvis-api-request",
          kind: "api.request",
          endpoint: detail.endpoint || "",
          method: detail.method || "GET"
        });
      });
      result.wrapped.push("lengrvis-api-request");
    } catch (error) {
      result.errors.push(`lengrvis-api-request: ${error instanceof Error ? error.message : String(error)}`);
    }

    if (window.lengrvis) {
      wrap("api.request", window.lengrvis.api, "request", (args) => {
        const request = args[0] || {};
        return {
          kind: "api.request",
          endpoint: request.endpoint || "",
          method: request.method || "GET"
        };
      });
      wrap("system.exportDiagnosticsPackage", window.lengrvis.system, "exportDiagnosticsPackage", () => ({
        kind: "system.exportDiagnosticsPackage",
        endpoint: "/api/system/diagnostics/export",
        method: "POST"
      }));
      wrap("runs.start", window.lengrvis.runs, "start", () => ({
        kind: "runs.start",
        endpoint: "/api/runs",
        method: "POST"
      }));
    }
    return result;
  });
}

(async () => {
  const playwrightModule = process.env.SMOKE_PLAYWRIGHT_MODULE;
  const remoteDebuggingPort = Number(process.env.SMOKE_CDP_PORT || "0");
  const timeoutMs = Math.max(5_000, Number(process.env.SMOKE_RENDERER_TIMEOUT_MS || "20000"));
  const deadline = Date.now() + timeoutMs;
  if (!playwrightModule || !remoteDebuggingPort) {
    finish("unsupported", "renderer DOM automation was not configured with Playwright and CDP inputs");
  }

  let chromium;
  try {
    ({ chromium } = require(playwrightModule));
  } catch (error) {
    finish("unsupported", `could not load Playwright from ${playwrightModule}: ${error instanceof Error ? error.message : String(error)}`);
  }

  let browser;
  try {
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${remoteDebuggingPort}`, { timeout: Math.min(5_000, remaining(deadline)) });
  } catch (error) {
    finish("unsupported", `CDP endpoint did not accept Playwright connection on 127.0.0.1:${remoteDebuggingPort}: ${error instanceof Error ? error.message : String(error)}`);
  }

  try {
    const page = await waitForRendererPage(browser, deadline);
    if (!page) {
      finish("unsupported", "CDP connected but no packaged renderer page was exposed");
    }

    const networkCalls = [];
    let observeReadOnlyClick = false;
    page.on("request", (request) => {
      try {
        if (!observeReadOnlyClick) return;
        const url = request.url();
        if (url.includes("/api/")) {
          networkCalls.push({ method: request.method(), endpoint: new URL(url).pathname });
        }
      } catch {
        // Ignore non-URL protocol noise.
      }
    });

    await page.waitForLoadState("domcontentloaded", { timeout: Math.min(5_000, remaining(deadline)) }).catch(() => undefined);
    await page.waitForSelector("body", { timeout: Math.min(5_000, remaining(deadline)) });
    const instrumentation = await instrumentBridge(page).catch((error) => ({
      hasBridge: false,
      wrapped: [],
      errors: [`bridge instrumentation failed: ${error instanceof Error ? error.message : String(error)}`]
    }));
    if (!instrumentation.hasBridge) {
      finish("unsupported", "packaged renderer did not expose the desktop preload bridge; read-only GUI evidence cannot rely on web fallback requests", {
        pageTitle: await page.title().catch(() => ""),
        pageUrl: page.url(),
        instrumentation
      });
    }

    const targetButtons = page.locator("button").filter({ hasText: systemCheckPattern });
    const targetCount = await targetButtons.count().catch(() => 0);
    if (targetCount < 1) {
      const buttonTexts = await page.locator("button").evaluateAll((buttons) =>
        buttons.map((button) => button.textContent || "").map((text) => text.replace(/\s+/g, " ").trim()).filter(Boolean).slice(0, 12)
      ).catch(() => []);
      finish("unsupported", "packaged renderer exposed CDP but no read-only system-check button was found", {
        pageTitle: await page.title().catch(() => ""),
        pageUrl: page.url(),
        buttonTexts
      });
    }

    const button = targetButtons.first();
    const clickedText = compactText(await button.innerText({ timeout: Math.min(2_000, remaining(deadline)) }).catch(() => "read-only system-check button"));
    await page.evaluate(() => {
      if (Array.isArray(window.__portableSmokeBridgeCalls)) window.__portableSmokeBridgeCalls.length = 0;
    }).catch(() => undefined);
    networkCalls.length = 0;
    observeReadOnlyClick = true;
    await button.click({ timeout: Math.min(5_000, remaining(deadline)) });
    await page.waitForTimeout(1_500);
    await page.waitForFunction(
      (patternSource) => Boolean(document.querySelector(".system-check-hero")) || new RegExp(patternSource).test(document.body?.innerText || ""),
      readOnlySystemCopyPattern.source,
      { timeout: Math.min(8_000, remaining(deadline)) }
    ).catch(() => undefined);

    const bodyText = compactText(await page.locator("body").innerText({ timeout: Math.min(4_000, remaining(deadline)) }).catch(() => ""));
    const hasSystemHero = await page.locator(".system-check-hero").count().then((count) => count > 0).catch(() => false);
    const sawReadOnlyCopy = hasSystemHero || readOnlySystemCopyPattern.test(bodyText);
    let bridgeCalls = [];
    let diagnosticsCalls = 0;
    const diagnosticsDeadline = Math.min(deadline, Date.now() + 8_000);
    while (Date.now() < diagnosticsDeadline) {
      bridgeCalls = await page.evaluate(() => Array.isArray(window.__portableSmokeBridgeCalls) ? window.__portableSmokeBridgeCalls : []).catch(() => []);
      const disallowedCalls = [...bridgeCalls, ...networkCalls].filter(isDisallowedReadOnlyApiCall);
      if (disallowedCalls.length > 0) {
        finish("fail", `read-only renderer API call outside allowlist after system-check click: ${disallowedCalls.map((call) => `${call.method || "GET"} ${call.endpoint || call.path || call.kind}`).join("; ")}`, {
          clickedText,
          bridgeCalls,
          networkCalls,
          instrumentation
        });
      }
      diagnosticsCalls = [...bridgeCalls, ...networkCalls].filter((call) => normalizeEndpoint(call.endpoint || call.path) === "/api/system/diagnostics").length;
      if (diagnosticsCalls > 0) {
        break;
      }
      await page.waitForTimeout(250);
    }
    if (!sawReadOnlyCopy) {
      finish("unsupported", "system-check click ran, but expected system information/read-only diagnostics copy was not visible", {
        clickedText,
        bodyText,
        bridgeCalls,
        networkCalls,
        instrumentation
      });
    }

    if (diagnosticsCalls < 1) {
      finish("unsupported", "system-check click did not invoke /api/system/diagnostics through the packaged renderer", {
        clickedText,
        bodyText,
        diagnosticsCalls,
        bridgeCalls,
        networkCalls,
        instrumentation
      });
    }
    finish("pass", `clicked '${clickedText}' and observed packaged renderer /api/system/diagnostics plus read-only diagnostics copy`, {
      clickedText,
      bodyText,
      diagnosticsCalls,
      bridgeCalls,
      networkCalls,
      instrumentation
    });
  } finally {
    await browser?.close().catch(() => undefined);
  }
})().catch((error) => {
  finish("unsupported", `renderer DOM automation errored: ${error instanceof Error ? error.message : String(error)}`);
});
'@
    Set-Content -LiteralPath $automationScript -Value $automationSource -Encoding UTF8

    $previousPlaywrightModule = $env:SMOKE_PLAYWRIGHT_MODULE
    $previousCdpPort = $env:SMOKE_CDP_PORT
    $previousRendererTimeout = $env:SMOKE_RENDERER_TIMEOUT_MS
    try {
        $env:SMOKE_PLAYWRIGHT_MODULE = $playwrightModule
        $env:SMOKE_CDP_PORT = [string]$RemoteDebuggingPort
        $env:SMOKE_RENDERER_TIMEOUT_MS = [string]([Math]::Max(5000, $TimeoutSeconds * 1000))

        $nodeOutput = @(& $nodeCommand.Source $automationScript 2>&1)
        $nodeExitCode = $LASTEXITCODE
        $nodeText = ($nodeOutput | ForEach-Object { [string]$_ }) -join "`n"
        Set-Content -LiteralPath $automationLog -Value (Redact-SmokeText $nodeText) -Encoding UTF8

        try {
            $payload = $nodeText | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            return [pscustomobject]@{
                Ok = $false
                Status = if ($nodeExitCode -eq 1) { "fail" } else { "unsupported" }
                Message = "renderer DOM automation returned non-JSON output (exit=$nodeExitCode, log=$automationLog): $(Redact-SmokeText $nodeText)"
            }
        }

        return [pscustomobject]@{
            Ok = [bool]$payload.ok
            Status = [string]$payload.status
            Message = "$(Redact-SmokeText $payload.message) (log=$automationLog)"
            Payload = $payload
        }
    }
    finally {
        if ($null -ne $previousPlaywrightModule) { $env:SMOKE_PLAYWRIGHT_MODULE = $previousPlaywrightModule } else { Remove-Item Env:\SMOKE_PLAYWRIGHT_MODULE -ErrorAction SilentlyContinue }
        if ($null -ne $previousCdpPort) { $env:SMOKE_CDP_PORT = $previousCdpPort } else { Remove-Item Env:\SMOKE_CDP_PORT -ErrorAction SilentlyContinue }
        if ($null -ne $previousRendererTimeout) { $env:SMOKE_RENDERER_TIMEOUT_MS = $previousRendererTimeout } else { Remove-Item Env:\SMOKE_RENDERER_TIMEOUT_MS -ErrorAction SilentlyContinue }
    }
}

function ConvertTo-SmokeJsonText {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) {
        return ""
    }

    try {
        $json = ConvertTo-Json -InputObject $Value -Depth 32 -Compress
        if ($null -eq $json) {
            return ""
        }
        return [string]$json
    }
    catch {
        return [string]$Value
    }
}

function Invoke-PortableNaturalLanguageDomAutomation {
    param(
        [int]$RemoteDebuggingPort,
        [string]$RunRoot,
        [int]$TimeoutSeconds
    )

    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCommand) {
        return [pscustomobject]@{
            Ok = $false
            Status = "unsupported"
            Message = "Node.js is not available, so packaged renderer natural-language DOM automation could not run."
        }
    }

    $playwrightModule = Join-Path $Root "desktop\node_modules\playwright"
    if (-not (Test-Path -LiteralPath $playwrightModule -PathType Container)) {
        return [pscustomobject]@{
            Ok = $false
            Status = "unsupported"
            Message = "Playwright module missing at $playwrightModule; run desktop dependencies before natural-language renderer DOM automation."
        }
    }

    $automationScript = Join-Path $RunRoot "portable-renderer-natural-language-evidence.cjs"
    $automationLog = Join-Path $RunRoot "portable-renderer-natural-language-evidence.log"
    $automationSource = @'
const naturalLanguagePrompt = "\u5e2e\u6211\u68c0\u67e5\u8fd9\u53f0\u7535\u8111";
const expectedPostEndpoints = new Set(["/api/chat", "/api/runs"]);
const forbiddenExactEndpoints = new Set(["/api/system/diagnostics/export"]);
const highRiskWritePatterns = [
  /^\/api\/tasks\/[^/]+\/(?:pause|resume|cancel|rollback)$/i,
  /^\/api\/approvals(?:\/|$)/i,
  /^\/api\/settings(?:\/|$)/i,
  /^\/api\/system\/diagnostics\/export$/i,
  /^\/api\/files(?:\/|$)/i,
  /^\/api\/apps(?:\/|$)/i
];
const visibleTaskOrFailurePattern = new RegExp([
  "\\u4efb\\u52a1",
  "Agent",
  "\\u8fdb\\u5ea6",
  "\\u5904\\u7406",
  "\\u5df2\\u5206\\u914d",
  "\\u5931\\u8d25",
  "\\u4e0d\\u53ef\\u7528",
  "failed",
  "unavailable"
].join("|"), "i");
const visibleSafeFailurePattern = new RegExp([
  "\\u670d\\u52a1\\u8fd8\\u6ca1\\u8fde\\u4e0a",
  "\\u6682\\u65f6\\u8fde\\u4e0d\\u4e0a",
  "\\u8fde\\u63a5\\u6062\\u590d",
  "\\u8fde\\u63a5\\u72b6\\u6001",
  "\\u79bb\\u7ebf",
  "\\u5931\\u8d25",
  "\\u4e0d\\u53ef\\u7528",
  "\\u6a21\\u578b",
  "offline",
  "not connected",
  "unavailable",
  "failed",
  "failure",
  "provider",
  "timeout",
  "error"
].join("|"), "i");
const refreshButtonSelector = 'button[aria-label="\\u5237\\u65b0"], button[title="\\u5237\\u65b0"], button[aria-label="Refresh"], button[title="Refresh"]';

function finish(status, message, extra = {}) {
  const payload = { ok: status === "pass", status, message, ...extra };
  process.stdout.write(JSON.stringify(payload));
  process.exit(status === "pass" ? 0 : status === "fail" ? 1 : 2);
}

function remaining(deadline) {
  return Math.max(250, deadline - Date.now());
}

function compactText(text, limit = 700) {
  return String(text || "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function normalizeEndpoint(value) {
  if (!value) return "";
  try {
    return new URL(String(value), "http://127.0.0.1").pathname;
  } catch {
    return String(value);
  }
}

function isExpectedNaturalLanguagePost(call) {
  const method = String(call.method || "GET").toUpperCase();
  const endpoint = normalizeEndpoint(call.endpoint);
  return (
    method === "POST" &&
    (expectedPostEndpoints.has(endpoint) || call.kind === "runs.start" || call.kind === "api.startRun")
  );
}

function isForbiddenNaturalLanguageCall(call) {
  const method = String(call.method || "GET").toUpperCase();
  const endpoint = normalizeEndpoint(call.endpoint);
  if (call.kind === "system.exportDiagnosticsPackage") return true;
  if (forbiddenExactEndpoints.has(endpoint)) return true;
  if (method === "GET") return false;
  if (isExpectedNaturalLanguagePost(call)) return false;
  return highRiskWritePatterns.some((pattern) => pattern.test(endpoint)) || method !== "GET";
}

async function waitForRendererPage(browser, deadline) {
  while (Date.now() < deadline) {
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const url = page.url();
        if (!url.startsWith("devtools://")) {
          return page;
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return null;
}

async function instrumentBridge(page) {
  return page.evaluate(() => {
    const result = { hasBridge: Boolean(window.lengrvis), wrapped: [], errors: [] };
    const calls = Array.isArray(window.__portableSmokeNaturalLanguageBridgeCalls)
      ? window.__portableSmokeNaturalLanguageBridgeCalls
      : [];
    try {
      Object.defineProperty(window, "__portableSmokeNaturalLanguageBridgeCalls", {
        value: calls,
        configurable: true
      });
    } catch {
      window.__portableSmokeNaturalLanguageBridgeCalls = calls;
    }

    function wrap(path, container, property, mapper) {
      if (!container || typeof container[property] !== "function") {
        result.errors.push(`${path} missing`);
        return;
      }
      const original = container[property];
      try {
        container[property] = function wrappedPortableSmokeNaturalLanguageBridgeCall(...args) {
          try {
            calls.push({ path, ...mapper(args) });
          } catch (error) {
            calls.push({ path, error: error instanceof Error ? error.message : String(error) });
          }
          return original.apply(this, args);
        };
        result.wrapped.push(path);
      } catch (error) {
        result.errors.push(`${path}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }

    try {
      window.addEventListener("lengrvis-api-request", (event) => {
        const detail = event?.detail || {};
        calls.push({
          path: "lengrvis-api-request",
          kind: "api.request",
          endpoint: detail.endpoint || "",
          method: detail.method || "GET"
        });
      });
      result.wrapped.push("lengrvis-api-request");
    } catch (error) {
      result.errors.push(`lengrvis-api-request: ${error instanceof Error ? error.message : String(error)}`);
    }

    if (window.lengrvis) {
      wrap("api.request", window.lengrvis.api, "request", (args) => {
        const request = args[0] || {};
        const endpoint = request.endpoint || "";
        const method = request.method || "GET";
        return {
          kind: String(method).toUpperCase() === "POST" && endpoint === "/api/runs" ? "api.startRun" : "api.request",
          endpoint,
          method
        };
      });
      wrap("api.startRun", window.lengrvis.api, "startRun", (args) => ({
        kind: "api.startRun",
        endpoint: "/api/runs",
        method: "POST",
        bodyKeys: args[0] && typeof args[0] === "object" ? Object.keys(args[0]).sort() : []
      }));
      wrap("system.exportDiagnosticsPackage", window.lengrvis.system, "exportDiagnosticsPackage", () => ({
        kind: "system.exportDiagnosticsPackage",
        endpoint: "/api/system/diagnostics/export",
        method: "POST"
      }));
      wrap("runs.start", window.lengrvis.runs, "start", (args) => ({
        kind: "runs.start",
        endpoint: "/api/runs",
        method: "POST",
        bodyKeys: args[0] && typeof args[0] === "object" ? Object.keys(args[0]).sort() : []
      }));
    }
    return result;
  });
}

function mergeObservedCalls(...groups) {
  const merged = [];
  const seen = new Set();
  for (const group of groups) {
    for (const call of Array.isArray(group) ? group : []) {
      const key = JSON.stringify([
        call.kind || "",
        call.method || "GET",
        normalizeEndpoint(call.endpoint),
        call.path || "",
        JSON.stringify(call.bodyKeys || [])
      ]);
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(call);
    }
  }
  return merged;
}

async function rendererCollectionSnapshot(page) {
  return page.evaluate(async () => {
    function collectionCount(payload) {
      if (Array.isArray(payload)) return payload.length;
      if (!payload || typeof payload !== "object") return 0;
      for (const key of ["runs", "tasks", "items", "results"]) {
        if (Array.isArray(payload[key])) return payload[key].length;
      }
      return 0;
    }

    const snapshot = { runsCount: 0, tasksCount: 0, ok: false, error: "" };
    try {
      if (!window.lengrvis?.api?.request) {
        snapshot.error = "api.request unavailable";
        return snapshot;
      }
      const runs = await window.lengrvis.api.request({ endpoint: "/api/runs", timeoutMs: 2000 });
      const tasks = await window.lengrvis.api.request({ endpoint: "/api/tasks", timeoutMs: 2000 });
      snapshot.runsCount = collectionCount(runs?.data);
      snapshot.tasksCount = collectionCount(tasks?.data);
      snapshot.ok = Boolean(runs?.ok || tasks?.ok);
    } catch (error) {
      snapshot.error = error instanceof Error ? error.message : String(error);
    }
    return snapshot;
  });
}

async function inferNaturalLanguagePostFromBackend(page, baseline) {
  if (!baseline || baseline.ok === false) return null;
  const snapshot = await rendererCollectionSnapshot(page).catch(() => null);
  if (!snapshot || snapshot.ok === false) return null;
  if (snapshot.runsCount > baseline.runsCount) {
    return {
      kind: "api.startRun",
      endpoint: "/api/runs",
      method: "POST",
      inferred: true,
      beforeRuns: baseline.runsCount,
      afterRuns: snapshot.runsCount
    };
  }
  if (snapshot.tasksCount > baseline.tasksCount) {
    return {
      kind: "api.request",
      endpoint: "/api/chat",
      method: "POST",
      inferred: true,
      beforeTasks: baseline.tasksCount,
      afterTasks: snapshot.tasksCount
    };
  }
  return null;
}

async function openHomeCommandDock(page, deadline) {
  const commandInput = page.locator(".office-command-dock textarea").first();
  if (await commandInput.count().catch(() => 0)) {
    return commandInput;
  }

  const homeButton = page.locator(".primary-nav .side-button").first();
  if (await homeButton.count().catch(() => 0)) {
    await homeButton.click({ timeout: Math.min(5_000, remaining(deadline)) }).catch(() => undefined);
  }
  await commandInput.waitFor({ timeout: Math.min(10_000, remaining(deadline)) });
  return commandInput;
}

async function rendererBackendSnapshot(page) {
  return page.evaluate(async () => {
    const snapshot = {
      hasBridge: Boolean(window.lengrvis),
      backendStatus: null,
      apiHealth: null,
      error: ""
    };

    try {
      if (window.lengrvis?.backend?.getStatus) {
        snapshot.backendStatus = await window.lengrvis.backend.getStatus();
      }
      if (window.lengrvis?.api?.request) {
        const health = await window.lengrvis.api.request({ endpoint: "/api/health", timeoutMs: 2000 });
        snapshot.apiHealth = {
          ok: Boolean(health?.ok),
          status: health?.status ?? null,
          message: health?.error?.message || health?.error || ""
        };
      }
    } catch (error) {
      snapshot.error = error instanceof Error ? error.message : String(error);
    }

    return snapshot;
  });
}

function rendererBackendReady(snapshot) {
  const backendStatus = snapshot?.backendStatus || {};
  const apiHealth = snapshot?.apiHealth || {};
  return Boolean(snapshot?.hasBridge) && (
    backendStatus.state === "running" ||
    backendStatus.health?.ok === true ||
    apiHealth.ok === true ||
    (Number(apiHealth.status) >= 200 && Number(apiHealth.status) < 300)
  );
}

async function waitForRendererBackendConnection(page, deadline) {
  let lastSnapshot = null;
  while (Date.now() < deadline) {
    lastSnapshot = await rendererBackendSnapshot(page).catch((error) => ({
      hasBridge: false,
      backendStatus: null,
      apiHealth: null,
      error: error instanceof Error ? error.message : String(error)
    }));
    if (rendererBackendReady(lastSnapshot)) {
      return { ok: true, snapshot: lastSnapshot };
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return { ok: false, snapshot: lastSnapshot };
}

async function commandDockSnapshot(page) {
  const snapshot = await page.evaluate(() => {
    const button = document.querySelector(".command-footer__send");
    const status = document.querySelector("#office-command-status");
    const connectionPill = document.querySelector(".connection-pill");
    const input = document.querySelector(".office-command-dock textarea");
    return {
      hasInput: Boolean(input),
      sendDisabled: !button || Boolean(button.disabled),
      sendBusy: button?.getAttribute("aria-busy") === "true",
      commandStatus: status?.textContent || "",
      connectionPill: connectionPill?.textContent || "",
      bodyText: document.body?.innerText || ""
    };
  }).catch((error) => ({
    hasInput: false,
    sendDisabled: true,
    sendBusy: false,
    commandStatus: "",
    connectionPill: "",
    bodyText: "",
    error: error instanceof Error ? error.message : String(error)
  }));

  return {
    ...snapshot,
    commandStatus: compactText(snapshot.commandStatus),
    connectionPill: compactText(snapshot.connectionPill),
    bodyText: compactText(snapshot.bodyText)
  };
}

async function clickRendererRefresh(page, deadline) {
  const refreshButton = page.locator(refreshButtonSelector).first();
  if (!(await refreshButton.count().catch(() => 0))) {
    return false;
  }
  if (await refreshButton.isDisabled().catch(() => true)) {
    return false;
  }
  await refreshButton.click({ timeout: Math.min(3_000, remaining(deadline)) }).catch(() => undefined);
  return true;
}

async function waitForCommandDockReady(page, input, deadline) {
  let lastSnapshot = await commandDockSnapshot(page);
  let lastRefreshAt = 0;
  while (Date.now() < deadline) {
    if (await input.inputValue().catch(() => "") !== naturalLanguagePrompt) {
      await input.fill(naturalLanguagePrompt, { timeout: Math.min(5_000, remaining(deadline)) }).catch(() => undefined);
    }
    lastSnapshot = await commandDockSnapshot(page);
    if (lastSnapshot.hasInput && !lastSnapshot.sendDisabled && !lastSnapshot.sendBusy) {
      return { ready: true, snapshot: lastSnapshot };
    }

    if (Date.now() - lastRefreshAt > 1_500) {
      lastRefreshAt = Date.now();
      await clickRendererRefresh(page, deadline);
    }
    await page.waitForTimeout(500);
  }
  return { ready: false, snapshot: lastSnapshot };
}

async function submitNaturalLanguagePrompt(page, sendButton, deadline) {
  try {
    await sendButton.click({ timeout: Math.min(5_000, remaining(deadline)) });
    return { method: "playwright.click" };
  } catch (clickError) {
    const forced = await sendButton.click({
      timeout: Math.min(2_000, remaining(deadline)),
      force: true
    }).then(() => true).catch(() => false);
    if (forced) {
      return {
        method: "playwright.click.force",
        primaryClickError: clickError instanceof Error ? clickError.message : String(clickError)
      };
    }

    const domClicked = await page.evaluate(() => {
      const button = document.querySelector(".command-footer__send");
      if (!button || button.disabled || button.getAttribute("aria-busy") === "true") {
        return false;
      }
      button.click();
      return true;
    }).catch(() => false);
    if (domClicked) {
      return {
        method: "dom.button.click",
        primaryClickError: clickError instanceof Error ? clickError.message : String(clickError)
      };
    }
    throw clickError;
  }
}

async function naturalLanguageOutcomeSnapshot(page, calls, networkCalls) {
  const dock = await commandDockSnapshot(page);
  const expectedPosts = calls.filter(isExpectedNaturalLanguagePost);
  const combinedVisibleText = `${dock.commandStatus} ${dock.connectionPill} ${dock.bodyText}`;
  return {
    dock,
    expectedPosts,
    visibleEvidence: visibleTaskOrFailurePattern.test(combinedVisibleText),
    visibleSafeFailure: visibleSafeFailurePattern.test(combinedVisibleText)
  };
}

async function waitForNaturalLanguageOutcome(page, deadline, networkCalls, backendSubmissionBaseline) {
  let lastOutcome = {
    dock: await commandDockSnapshot(page),
    expectedPosts: [],
    visibleEvidence: false,
    visibleSafeFailure: false
  };
  while (Date.now() < deadline) {
    const bridgeCalls = await page.evaluate(() =>
      Array.isArray(window.__portableSmokeNaturalLanguageBridgeCalls)
        ? window.__portableSmokeNaturalLanguageBridgeCalls
        : []
    ).catch(() => []);
    const inferredPost = await inferNaturalLanguagePostFromBackend(page, backendSubmissionBaseline);
    const calls = mergeObservedCalls(bridgeCalls, networkCalls, inferredPost ? [inferredPost] : []);
    lastOutcome = await naturalLanguageOutcomeSnapshot(page, calls, networkCalls);
    lastOutcome.bridgeCalls = mergeObservedCalls(bridgeCalls, inferredPost ? [inferredPost] : []);
    lastOutcome.calls = calls;
    if (lastOutcome.expectedPosts.length > 0) {
      return lastOutcome;
    }
    await page.waitForTimeout(500);
  }
  return lastOutcome;
}

(async () => {
  const playwrightModule = process.env.SMOKE_PLAYWRIGHT_MODULE;
  const remoteDebuggingPort = Number(process.env.SMOKE_CDP_PORT || "0");
  const timeoutMs = Math.max(5_000, Number(process.env.SMOKE_RENDERER_TIMEOUT_MS || "20000"));
  const deadline = Date.now() + timeoutMs;
  if (!playwrightModule || !remoteDebuggingPort) {
    finish("unsupported", "natural-language renderer DOM automation was not configured with Playwright and CDP inputs");
  }

  let chromium;
  try {
    ({ chromium } = require(playwrightModule));
  } catch (error) {
    finish("unsupported", `could not load Playwright from ${playwrightModule}: ${error instanceof Error ? error.message : String(error)}`);
  }

  let browser;
  try {
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${remoteDebuggingPort}`, { timeout: Math.min(5_000, remaining(deadline)) });
  } catch (error) {
    finish("unsupported", `CDP endpoint did not accept Playwright connection for natural-language evidence on 127.0.0.1:${remoteDebuggingPort}: ${error instanceof Error ? error.message : String(error)}`);
  }

  try {
    const page = await waitForRendererPage(browser, deadline);
    if (!page) {
      finish("unsupported", "CDP connected but no packaged renderer page was exposed for natural-language evidence");
    }

    const networkCalls = [];
    page.on("request", (request) => {
      try {
        const url = request.url();
        if (url.includes("/api/")) {
          networkCalls.push({ method: request.method(), endpoint: new URL(url).pathname });
        }
      } catch {
        // Ignore non-URL protocol noise.
      }
    });

    await page.waitForLoadState("domcontentloaded", { timeout: Math.min(5_000, remaining(deadline)) }).catch(() => undefined);
    await page.waitForSelector("body", { timeout: Math.min(5_000, remaining(deadline)) });
    const backendConnection = await waitForRendererBackendConnection(page, Math.min(deadline, Date.now() + 8_000));
    await page.reload({ waitUntil: "domcontentloaded", timeout: Math.min(8_000, remaining(deadline)) }).catch(() => undefined);
    await page.waitForSelector("body", { timeout: Math.min(5_000, remaining(deadline)) });
    const backendConnectionAfterReload = await waitForRendererBackendConnection(page, Math.min(deadline, Date.now() + 8_000));

    const instrumentation = await instrumentBridge(page).catch((error) => ({
      hasBridge: false,
      wrapped: [],
      errors: [`bridge instrumentation failed: ${error instanceof Error ? error.message : String(error)}`]
    }));
    if (!instrumentation.hasBridge) {
      finish("unsupported", "packaged renderer did not expose the desktop preload bridge; natural-language GUI evidence cannot rely on web fallback state", {
        pageTitle: await page.title().catch(() => ""),
        pageUrl: page.url(),
        backendConnection: backendConnectionAfterReload,
        instrumentation
      });
    }

    let input;
    try {
      input = await openHomeCommandDock(page, deadline);
    } catch (error) {
      finish("unsupported", `packaged renderer did not expose the home command dock for natural-language evidence: ${error instanceof Error ? error.message : String(error)}`, {
        pageTitle: await page.title().catch(() => ""),
        pageUrl: page.url(),
        backendConnection: backendConnectionAfterReload,
        instrumentation
      });
    }

    const commandReady = await waitForCommandDockReady(page, input, deadline);
    const sendButton = page.locator(".command-footer__send").first();
    await sendButton.waitFor({ timeout: Math.min(5_000, remaining(deadline)) });

    if (!commandReady.ready || await sendButton.isDisabled().catch(() => true)) {
      const commandStatus = compactText(await page.locator("#office-command-status").innerText({ timeout: Math.min(2_000, remaining(deadline)) }).catch(() => ""));
      const disabledText = `${commandReady.snapshot?.commandStatus || ""} ${commandReady.snapshot?.connectionPill || ""} ${commandReady.snapshot?.bodyText || ""} ${commandStatus}`;
      if (visibleSafeFailurePattern.test(disabledText)) {
        finish("unsupported", "natural-language command dock displayed clear visible safe failure before submit; no packaged task submission was possible", {
          prompt: naturalLanguagePrompt,
          visibleSafeFailure: true,
          commandStatus,
          commandReady,
          backendConnection: backendConnectionAfterReload,
          instrumentation
        });
      }
      finish("unsupported", "natural-language prompt was filled, but packaged command dock send remained disabled after renderer/backend readiness wait", {
        prompt: naturalLanguagePrompt,
        commandStatus,
        commandReady,
        backendConnection: backendConnectionAfterReload,
        instrumentation
      });
    }

    const backendSubmissionBaseline = await rendererCollectionSnapshot(page).catch(() => ({
      runsCount: 0,
      tasksCount: 0,
      ok: false,
      error: "baseline snapshot failed"
    }));
    await page.evaluate(() => {
      if (Array.isArray(window.__portableSmokeNaturalLanguageBridgeCalls)) window.__portableSmokeNaturalLanguageBridgeCalls.length = 0;
    }).catch(() => undefined);
    networkCalls.length = 0;
    const submitAttempt = await submitNaturalLanguagePrompt(page, sendButton, deadline);
    const outcome = await waitForNaturalLanguageOutcome(page, Math.min(deadline, Date.now() + 15_000), networkCalls, backendSubmissionBaseline);
    await page.waitForTimeout(750);

    const bridgeCalls = await page.evaluate(() =>
      Array.isArray(window.__portableSmokeNaturalLanguageBridgeCalls)
        ? window.__portableSmokeNaturalLanguageBridgeCalls
        : []
    ).catch(() => []);
    const observedBridgeCalls = mergeObservedCalls(outcome.bridgeCalls, bridgeCalls);
    const calls = mergeObservedCalls(observedBridgeCalls, outcome.calls, networkCalls);
    const forbiddenCalls = calls.filter(isForbiddenNaturalLanguageCall);
    if (forbiddenCalls.length > 0) {
      finish("fail", `forbidden high-risk renderer call(s) observed after natural-language prompt: ${forbiddenCalls.map((call) => `${call.method || "GET"} ${call.endpoint || call.path || call.kind}`).join("; ")}`, {
        prompt: naturalLanguagePrompt,
        bridgeCalls: observedBridgeCalls,
        networkCalls,
        backendConnection: backendConnectionAfterReload,
        instrumentation,
        submitAttempt
      });
    }

    const expectedPosts = calls.filter(isExpectedNaturalLanguagePost);
    if (expectedPosts.length < 1) {
      const commandStatus = compactText(await page.locator("#office-command-status").innerText({ timeout: Math.min(2_000, remaining(deadline)) }).catch(() => ""));
      if (outcome.visibleSafeFailure) {
        finish("unsupported", "natural-language prompt produced clear visible safe failure copy in the packaged command dock, but no /api/chat or /api/runs POST was observed", {
          prompt: naturalLanguagePrompt,
          visibleSafeFailure: true,
          bodyText: outcome.dock?.bodyText || "",
          commandStatus,
          bridgeCalls: observedBridgeCalls,
          networkCalls,
          backendConnection: backendConnectionAfterReload,
          instrumentation,
          submitAttempt
        });
      }
      finish("unsupported", "natural-language prompt was clicked, but no /api/chat or /api/runs POST was observed through the packaged renderer bridge", {
        prompt: naturalLanguagePrompt,
        commandStatus,
        bridgeCalls: observedBridgeCalls,
        networkCalls,
        backendConnection: backendConnectionAfterReload,
        instrumentation,
        submitAttempt
      });
    }

    const bodyText = compactText(await page.locator("body").innerText({ timeout: Math.min(4_000, remaining(deadline)) }).catch(() => ""));
    const commandStatus = compactText(await page.locator("#office-command-status").innerText({ timeout: Math.min(2_000, remaining(deadline)) }).catch(() => ""));
    const visibleEvidence = visibleTaskOrFailurePattern.test(bodyText) || visibleTaskOrFailurePattern.test(commandStatus);
    finish("pass", `submitted natural-language prompt through packaged command dock and observed expected POST ${expectedPosts.map((call) => normalizeEndpoint(call.endpoint)).join(",")}; backend task/run evidence will be verified separately`, {
      prompt: naturalLanguagePrompt,
      bodyText,
      commandStatus,
      visibleEvidence,
      bridgeCalls: observedBridgeCalls,
      networkCalls,
      backendConnection: backendConnectionAfterReload,
      instrumentation,
      submitAttempt
    });
  } finally {
    await browser?.close().catch(() => undefined);
  }
})().catch((error) => {
  finish("unsupported", `natural-language renderer DOM automation errored: ${error instanceof Error ? error.message : String(error)}`);
});
'@
    Set-Content -LiteralPath $automationScript -Value $automationSource -Encoding UTF8

    $previousPlaywrightModule = $env:SMOKE_PLAYWRIGHT_MODULE
    $previousCdpPort = $env:SMOKE_CDP_PORT
    $previousRendererTimeout = $env:SMOKE_RENDERER_TIMEOUT_MS
    try {
        $env:SMOKE_PLAYWRIGHT_MODULE = $playwrightModule
        $env:SMOKE_CDP_PORT = [string]$RemoteDebuggingPort
        $env:SMOKE_RENDERER_TIMEOUT_MS = [string]([Math]::Max(5000, $TimeoutSeconds * 1000))

        $nodeOutput = @(& $nodeCommand.Source $automationScript 2>&1)
        $nodeExitCode = $LASTEXITCODE
        $nodeText = ($nodeOutput | ForEach-Object { [string]$_ }) -join "`n"
        Set-Content -LiteralPath $automationLog -Value (Redact-SmokeText $nodeText) -Encoding UTF8

        try {
            $payload = $nodeText | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            return [pscustomobject]@{
                Ok = $false
                Status = if ($nodeExitCode -eq 1) { "fail" } else { "unsupported" }
                Message = "natural-language renderer DOM automation returned non-JSON output (exit=$nodeExitCode, log=$automationLog): $(Redact-SmokeText $nodeText)"
            }
        }

        return [pscustomobject]@{
            Ok = [bool]$payload.ok
            Status = [string]$payload.status
            Message = "$(Redact-SmokeText $payload.message) (log=$automationLog)"
            Payload = $payload
        }
    }
    finally {
        if ($null -ne $previousPlaywrightModule) { $env:SMOKE_PLAYWRIGHT_MODULE = $previousPlaywrightModule } else { Remove-Item Env:\SMOKE_PLAYWRIGHT_MODULE -ErrorAction SilentlyContinue }
        if ($null -ne $previousCdpPort) { $env:SMOKE_CDP_PORT = $previousCdpPort } else { Remove-Item Env:\SMOKE_CDP_PORT -ErrorAction SilentlyContinue }
        if ($null -ne $previousRendererTimeout) { $env:SMOKE_RENDERER_TIMEOUT_MS = $previousRendererTimeout } else { Remove-Item Env:\SMOKE_RENDERER_TIMEOUT_MS -ErrorAction SilentlyContinue }
    }
}

function Test-PortableNaturalLanguageTaskEvidence {
    param(
        [string]$BackendUrl,
        [string]$ExpectedDataDir,
        [string]$DesktopApiToken,
        [int]$TimeoutSeconds = 20
    )

    $relatedIntentPattern = "(?i)(\u5e2e\u6211\u68c0\u67e5\u8fd9\u53f0\u7535\u8111|\u68c0\u67e5\u8fd9\u53f0\u7535\u8111|\u68c0\u67e5\u7535\u8111|\u7535\u8111\u72b6\u6001|system status|computer status|diagnostics)"
    $readOnlyPattern = "(?i)(system\.diagnostics|read[- ]only|\u53ea\u8bfb|local_only|\u7cfb\u7edf\u8bca\u65ad)"
    $diagnosticsEvidencePattern = "(?i)(system\.diagnostics|local_ai|local_only|diagnostic_scope|top_processes|startup_items|\u7cfb\u7edf\u8bca\u65ad)"
    $safeFailurePattern = "(?i)(failed|failure|unavailable|backend|provider|timeout|error|offline|not connected|service|paused|full_backend_backgrounding|\u5931\u8d25|\u4e0d\u53ef\u7528|\u7a0d\u540e|\u670d\u52a1|\u6a21\u578b|\u8fde\u4e0d\u4e0a|\u672a\u8fde\u63a5|\u79bb\u7ebf)"
    $highRiskPattern = "(?i)(R[2-4]_|requires_user_approval\\?[""']?\s*:\s*true|permanent_delete\\?[""']?\s*:\s*true)"
    $deadline = (Get-Date).AddSeconds([Math]::Max(5, $TimeoutSeconds))
    $lastObservation = "not attempted"

    while ((Get-Date) -lt $deadline) {
        try {
            $tasksPayload = Get-SmokeJson -Url "$BackendUrl/api/tasks" -DesktopApiToken $DesktopApiToken
            $runsPayload = Get-SmokeJson -Url "$BackendUrl/api/runs" -DesktopApiToken $DesktopApiToken
            $messagesPayload = Get-SmokeJson -Url "$BackendUrl/api/chat/messages" -DesktopApiToken $DesktopApiToken
        }
        catch {
            return [pscustomobject]@{
                Ok = $false
                Status = "unsupported"
                Message = "could not inspect natural-language backend task state: $(Redact-SmokeText $_.Exception.Message)"
            }
        }

        $exportDir = Join-Path $ExpectedDataDir "diagnostic-packages"
        $exportFiles = @()
        if (Test-Path -LiteralPath $exportDir -PathType Container) {
            $exportFiles = @(Get-ChildItem -LiteralPath $exportDir -File -ErrorAction SilentlyContinue)
        }
        if ($exportFiles.Count -ne 0) {
            return [pscustomobject]@{
                Ok = $false
                Status = "fail"
                Message = "natural-language prompt created diagnostics export package(s): diagnostic-packages=$($exportFiles.Count)"
            }
        }

        $tasks = @($tasksPayload)
        $runs = @($runsPayload)
        $messages = @($messagesPayload)
        $relatedTasks = @($tasks | Where-Object { [regex]::IsMatch((ConvertTo-SmokeJsonText $_), $relatedIntentPattern) })
        $relatedRuns = @($runs | Where-Object { [regex]::IsMatch((ConvertTo-SmokeJsonText $_), $relatedIntentPattern) })
        $chatText = ConvertTo-SmokeJsonText $messages
        if ($relatedTasks.Count -eq 0 -and $tasks.Count -eq 1 -and ($messages.Count -gt 0 -or [regex]::IsMatch($chatText, $relatedIntentPattern))) {
            $relatedTasks = @($tasks)
        }
        if ($relatedRuns.Count -eq 0 -and $runs.Count -eq 1 -and ($messages.Count -gt 0 -or [regex]::IsMatch($chatText, $relatedIntentPattern))) {
            $relatedRuns = @($runs)
        }
        $lastObservation = "tasks=$($tasks.Count), relatedTasks=$($relatedTasks.Count), runs=$($runs.Count), relatedRuns=$($relatedRuns.Count), chat messages=$($messages.Count), diagnostic-packages=$($exportFiles.Count)"

        foreach ($task in $relatedTasks) {
            $taskId = [string]$task.id
            $taskText = ConvertTo-SmokeJsonText $task
            $timelineText = ""
            $taskDetailText = ""
            if ($taskId) {
                try {
                    $taskDetailText = ConvertTo-SmokeJsonText (Get-SmokeJson -Url "$BackendUrl/api/tasks/$taskId" -DesktopApiToken $DesktopApiToken)
                }
                catch {
                    $taskDetailText = ""
                }
                try {
                    $timelineText = ConvertTo-SmokeJsonText (Get-SmokeJson -Url "$BackendUrl/api/tasks/$taskId/timeline" -DesktopApiToken $DesktopApiToken)
                }
                catch {
                    $timelineText = ""
                }
            }
            $combined = "$taskText $taskDetailText $timelineText"
            if ([regex]::IsMatch($combined, $highRiskPattern)) {
                return [pscustomobject]@{
                    Ok = $false
                    Status = "fail"
                    Message = "natural-language task $taskId showed high-risk intent instead of read-only diagnostics ($lastObservation)"
                }
            }
            $taskStatus = [string]$task.status
            if ($taskStatus -notin @("failed", "cancelled") -and [regex]::IsMatch($combined, $readOnlyPattern) -and [regex]::IsMatch($combined, $diagnosticsEvidencePattern)) {
                return [pscustomobject]@{
                    Ok = $true
                    Status = "pass"
                    Message = "natural-language prompt created read-only/system diagnostics task $taskId ($lastObservation)"
                }
            }
            $taskSummary = [string]$task.final_summary
            if ($taskStatus -in @("failed", "cancelled") -and $taskSummary -and [regex]::IsMatch($taskSummary, $safeFailurePattern)) {
                $lastObservation = "$lastObservation, safeFailureTask=$taskId status=$taskStatus"
            }
        }

        foreach ($run in $relatedRuns) {
            $runId = [string]$run.run_id
            if (-not $runId -and ($run.PSObject.Properties.Name -contains "id")) {
                $runId = [string]$run.id
            }
            $runText = ConvertTo-SmokeJsonText $run
            $runTimelineText = ""
            $runProgressText = ""
            $runTaskText = ""
            $runTaskId = [string]$run.task_id
            if ($runId) {
                try {
                    $runTimelineText = ConvertTo-SmokeJsonText (Get-SmokeJson -Url "$BackendUrl/api/runs/$runId/timeline" -DesktopApiToken $DesktopApiToken)
                }
                catch {
                    $runTimelineText = ""
                }
                try {
                    $runProgressText = ConvertTo-SmokeJsonText (Get-SmokeJson -Url "$BackendUrl/api/runs/$runId/progress" -DesktopApiToken $DesktopApiToken)
                }
                catch {
                    $runProgressText = ""
                }
            }
            if ($runTaskId) {
                try {
                    $runTaskText = ConvertTo-SmokeJsonText (Get-SmokeJson -Url "$BackendUrl/api/tasks/$runTaskId" -DesktopApiToken $DesktopApiToken)
                }
                catch {
                    $runTaskText = ""
                }
                try {
                    $runTaskText = "$runTaskText $(ConvertTo-SmokeJsonText (Get-SmokeJson -Url "$BackendUrl/api/tasks/$runTaskId/timeline" -DesktopApiToken $DesktopApiToken))"
                }
                catch {
                    $runTaskText = "$runTaskText"
                }
            }
            $combined = "$runText $runTimelineText $runProgressText $runTaskText"
            if ([regex]::IsMatch($combined, $highRiskPattern)) {
                return [pscustomobject]@{
                    Ok = $false
                    Status = "fail"
                    Message = "natural-language run $runId showed high-risk intent instead of read-only diagnostics ($lastObservation)"
                }
            }
            $runPhase = [string]$run.phase
            if ($runPhase -notin @("failed", "denied", "cancelled", "paused") -and [regex]::IsMatch($combined, $readOnlyPattern) -and [regex]::IsMatch($combined, $diagnosticsEvidencePattern)) {
                return [pscustomobject]@{
                    Ok = $true
                    Status = "pass"
                    Message = "natural-language prompt created read-only/system diagnostics run $runId ($lastObservation)"
                }
            }
            $runError = [string]$run.error
            if ($runPhase -in @("failed", "denied", "cancelled", "paused") -and [regex]::IsMatch("$runError $runTimelineText $runProgressText", $safeFailurePattern)) {
                $lastObservation = "$lastObservation, safeFailureRun=$runId phase=$runPhase"
            }
        }

        if ($relatedTasks.Count -eq 0 -and $relatedRuns.Count -eq 0 -and [regex]::IsMatch($chatText, $relatedIntentPattern) -and [regex]::IsMatch($chatText, $safeFailurePattern)) {
            $lastObservation = "$lastObservation, safeFailureChatWithoutTaskOrRun=true"
        }

        Start-Sleep -Milliseconds 750
    }

    return [pscustomobject]@{
        Ok = $false
        Status = "unsupported"
        Message = "natural-language prompt did not expose concrete read-only/system diagnostics task or run evidence within $TimeoutSeconds seconds ($lastObservation)"
    }
}

function Test-RendererDomEvidence {
    param(
        [int]$RemoteDebuggingPort,
        [string]$BackendUrl,
        [string]$ExpectedDataDir,
        [string]$DesktopApiToken,
        [string]$RunRoot,
        [int]$TimeoutSeconds
    )

    $automation = Invoke-PortableRendererDomAutomation `
        -RemoteDebuggingPort $RemoteDebuggingPort `
        -RunRoot $RunRoot `
        -TimeoutSeconds $TimeoutSeconds

    if (-not $automation.Ok) {
        return $automation
    }

    $sideEffects = Test-NoPortableWriteSideEffects `
        -BackendUrl $BackendUrl `
        -ExpectedDataDir $ExpectedDataDir `
        -DesktopApiToken $DesktopApiToken

    if (-not $sideEffects.Ok) {
        return $sideEffects
    }

    $naturalLanguageDom = Invoke-PortableNaturalLanguageDomAutomation `
        -RemoteDebuggingPort $RemoteDebuggingPort `
        -RunRoot $RunRoot `
        -TimeoutSeconds $TimeoutSeconds

    $naturalLanguageStatus = $naturalLanguageDom.Status
    $naturalLanguageMessage = $naturalLanguageDom.Message
    $naturalLanguagePayload = $naturalLanguageDom.Payload
    if ($naturalLanguageDom.Status -eq "fail") {
        return [pscustomobject]@{
            Ok = $false
            Status = "fail"
            Message = "natural-language renderer DOM evidence failed: $naturalLanguageMessage"
            Payload = $naturalLanguagePayload
        }
    }
    elseif ($naturalLanguageDom.Ok) {
        $naturalLanguageBackend = Test-PortableNaturalLanguageTaskEvidence `
            -BackendUrl $BackendUrl `
            -ExpectedDataDir $ExpectedDataDir `
            -DesktopApiToken $DesktopApiToken `
            -TimeoutSeconds $TimeoutSeconds

        $naturalLanguageStatus = $naturalLanguageBackend.Status
        $naturalLanguageMessage = "$naturalLanguageMessage; $($naturalLanguageBackend.Message)"
        if ($naturalLanguageBackend.Status -eq "fail") {
            return [pscustomobject]@{
                Ok = $false
                Status = "fail"
                Message = "natural-language backend evidence failed: $naturalLanguageMessage"
                Payload = $naturalLanguagePayload
            }
        }
    }
    elseif ($naturalLanguageDom.Status -eq "unsupported") {
        $visibleSafeFailure = $false
        if ($naturalLanguagePayload -and ($naturalLanguagePayload.PSObject.Properties.Name -contains "visibleSafeFailure")) {
            $visibleSafeFailure = [bool]$naturalLanguagePayload.visibleSafeFailure
        }
        if ($visibleSafeFailure) {
            $safeFailureSideEffects = Test-NoPortableWriteSideEffects `
                -BackendUrl $BackendUrl `
                -ExpectedDataDir $ExpectedDataDir `
                -DesktopApiToken $DesktopApiToken

            if (-not $safeFailureSideEffects.Ok) {
                return [pscustomobject]@{
                    Ok = $false
                    Status = "fail"
                    Message = "natural-language visible safe failure side-effect check failed: $($safeFailureSideEffects.Message)"
                    Payload = $naturalLanguagePayload
                }
            }

            $naturalLanguageMessage = "$naturalLanguageMessage; visible safe failure is not accepted as natural-language task evidence; $($safeFailureSideEffects.Message)"
        }

        $naturalLanguageBridgeAvailable = $false
        if ($naturalLanguagePayload -and ($naturalLanguagePayload.PSObject.Properties.Name -contains "instrumentation")) {
            $instrumentation = $naturalLanguagePayload.instrumentation
            if ($instrumentation -and ($instrumentation.PSObject.Properties.Name -contains "hasBridge")) {
                $naturalLanguageBridgeAvailable = [bool]$instrumentation.hasBridge
            }
        }

        if ($naturalLanguageBridgeAvailable) {
            $naturalLanguageBackend = Test-PortableNaturalLanguageTaskEvidence `
                -BackendUrl $BackendUrl `
                -ExpectedDataDir $ExpectedDataDir `
                -DesktopApiToken $DesktopApiToken `
                -TimeoutSeconds $TimeoutSeconds

            $naturalLanguageStatus = $naturalLanguageBackend.Status
            $naturalLanguageMessage = "$naturalLanguageMessage; $($naturalLanguageBackend.Message)"
            if ($naturalLanguageBackend.Status -eq "fail") {
                return [pscustomobject]@{
                    Ok = $false
                    Status = "fail"
                    Message = "natural-language backend evidence failed after renderer bridge submission attempt: $naturalLanguageMessage"
                    Payload = $naturalLanguagePayload
                }
            }
        }
    }

    return [pscustomobject]@{
        Ok = $true
        Status = "pass"
        Message = "$($automation.Message); $($sideEffects.Message)"
        Payload = $automation.Payload
        NaturalLanguageStatus = $naturalLanguageStatus
        NaturalLanguageMessage = $naturalLanguageMessage
        NaturalLanguagePayload = $naturalLanguagePayload
    }
}

function Get-SmokeWindowProcess {
    param([int[]]$ProcessIds)

    foreach ($processId in $ProcessIds) {
        try {
            $process = Get-Process -Id $processId -ErrorAction Stop
            if ($process.MainWindowHandle -and $process.MainWindowHandle -ne [IntPtr]::Zero) {
                return $process
            }
        }
        catch {
        }
    }
    return $null
}

function Stop-LaunchedProcesses {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$LauncherPath,
        [string]$BackendPath,
        [int[]]$BaselineLauncherIds,
        [int[]]$BaselineBackendIds
    )

    $processIds = @(Get-LaunchedProcessIds `
        -Process $Process `
        -LauncherPath $LauncherPath `
        -BackendPath $BackendPath `
        -BaselineLauncherIds $BaselineLauncherIds `
        -BaselineBackendIds $BaselineBackendIds)
    $protectedProcessIds = Get-CurrentProcessFamilyIds
    $processIds = @($processIds | Where-Object { -not $protectedProcessIds.Contains([int]$_) } | Select-Object -Unique)
    if ($processIds.Count -gt 0) {
        Write-SmokeStatus "info" "closing portable smoke process ids: $($processIds -join ',')" ([ConsoleColor]::Cyan)
    }

    foreach ($processId in $processIds) {
        try {
            $process = Get-Process -Id $processId -ErrorAction Stop
            if ($process.MainWindowHandle -and $process.MainWindowHandle -ne [IntPtr]::Zero) {
                [void]$process.CloseMainWindow()
            }
        }
        catch {
        }
    }

    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $deadline) {
        $remaining = @($processIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($remaining.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }

    foreach ($processId in $processIds) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

if ($env:OS -and $env:OS -notmatch "Windows") {
    Exit-SmokeBlocked "portable first-screen smoke requires Windows; current OS is $($env:OS)."
}

$portablePath = Resolve-SmokePath $PortableDir
$launcherPath = Join-Path $portablePath "Lengrvis.exe"
$backendPath = Join-Path $portablePath "resources\backend\backend.exe"

if (-not (Test-Path -LiteralPath $portablePath -PathType Container)) {
    Exit-SmokeBlocked "portable artifact directory missing: $portablePath"
}
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    Exit-SmokeBlocked "portable launcher missing: $launcherPath"
}
if (-not (Test-Path -LiteralPath $backendPath -PathType Leaf)) {
    Exit-SmokeBlocked "portable backend missing: $backendPath"
}

$workspacePath = Resolve-SmokePath $Workspace
$runId = "run-{0}-{1}-{2}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $PID, (([guid]::NewGuid().ToString("N")).Substring(0, 8))
$runRoot = Join-Path $workspacePath $runId
$stateDir = Join-Path $runRoot "state"
$dataDir = Join-Path $runRoot "data"
$profileDir = Join-Path $runRoot "electron-user-data"
$stdoutLog = Join-Path $runRoot "portable.stdout.log"
$stderrLog = Join-Path $runRoot "portable.stderr.log"
$script:SmokeStatusLog = Join-Path $runRoot "portable.status.log"
New-Item -ItemType Directory -Path $stateDir, $dataDir, $profileDir -Force | Out-Null
New-Item -ItemType File -Path $stdoutLog, $stderrLog -Force | Out-Null

$backendPort = Get-FreeTcpPort
$remoteDebuggingPort = Get-FreeTcpPort
$backendUrl = "http://127.0.0.1:$backendPort"
$healthUrl = "$backendUrl/health"
$diagnosticsUrl = "$backendUrl/api/system/diagnostics"
$desktopApiToken = "portable-smoke-$(([guid]::NewGuid().ToString("N")))"
$baselineLauncherIds = @(Get-ExecutableProcessIds -ExecutablePath $launcherPath)
$baselineBackendIds = @(Get-ExecutableProcessIds -ExecutablePath $backendPath)

$envVars = @{
    LENGRVIS_BACKEND_HOST = "127.0.0.1"
    LENGRVIS_BACKEND_PORT = [string]$backendPort
    LENGRVIS_BACKEND_URL = $backendUrl
    LENGRVIS_BACKEND_SERVICE_DISABLED = "1"
    LENGRVIS_BACKEND_LOG_LEVEL = "warning"
    LENGRVIS_CONFIG_DIR = $stateDir
    LENGRVIS_DATA_DIR = $dataDir
    LENGRVIS_STATE_DIR = $stateDir
    LENGRVIS_PROVIDER_NAME = "mock"
    LENGRVIS_API_KEY = ""
    LENGRVIS_DESKTOP_API_TOKEN = $desktopApiToken
    LENGRVIS_ENV = "smoke"
}

$process = $null
$started = $false
$passed = $false
$passReason = ""
$lastProbe = "not attempted"
$lastDiagnosticsProbe = "not attempted"
$windowObserved = $false
$windowProcessId = $null
$windowTitle = ""
$diagnosticsObserved = $false
$diagnosticsPassReason = ""
$rendererEvidenceStatus = "not_attempted"
$rendererEvidenceMessage = "renderer DOM automation not attempted"
$naturalLanguageEvidenceStatus = "not_attempted"
$naturalLanguageEvidenceMessage = "natural-language renderer DOM automation not attempted"
$previousEnv = @{}
$fatalError = ""

try {
    foreach ($key in $envVars.Keys) {
        $envPath = "Env:\$key"
        $previousEnv[$key] = if (Test-Path $envPath) { (Get-Item $envPath).Value } else { $null }
        Set-Item -Path $envPath -Value ([string]$envVars[$key])
    }

    $process = Start-Process `
        -FilePath $launcherPath `
        -ArgumentList @(
            "--user-data-dir=$profileDir",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=$remoteDebuggingPort",
            "--remote-allow-origins=*"
        ) `
        -WorkingDirectory $portablePath `
        -PassThru
    if (-not $process) {
        Exit-SmokeFailed "portable launcher did not return a process handle: $launcherPath"
    }

    $started = $true
    Write-SmokeStatus "info" "started portable launcher pid $($process.Id); health probe $healthUrl; read-only diagnostics probe $diagnosticsUrl; renderer CDP http://127.0.0.1:$remoteDebuggingPort; temp state $runRoot" ([ConsoleColor]::Cyan)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $launchedIds = @(Get-LaunchedProcessIds `
            -Process $process `
            -LauncherPath $launcherPath `
            -BackendPath $backendPath `
            -BaselineLauncherIds $baselineLauncherIds `
            -BaselineBackendIds $baselineBackendIds)

        $windowProcess = Get-SmokeWindowProcess -ProcessIds $launchedIds
        if ($windowProcess) {
            if (-not $windowObserved) {
                $windowObserved = $true
                $windowProcessId = [int]$windowProcess.Id
                $windowTitle = Redact-SmokeText $windowProcess.MainWindowTitle
                Write-SmokeStatus "info" "portable window appeared for pid $windowProcessId; continuing until backend read-only diagnostics passes" ([ConsoleColor]::Cyan)
            }
        }

        if (Test-BackendHealth -HealthUrl $healthUrl) {
            $diagnosticsProbe = Test-ReadOnlyDiagnostics -DiagnosticsUrl $diagnosticsUrl -ExpectedDataDir $dataDir -DesktopApiToken $desktopApiToken
            $lastDiagnosticsProbe = $diagnosticsProbe.Message
            if ($diagnosticsProbe.Ok -and $windowObserved) {
                $passed = $true
                $diagnosticsPassReason = "$($diagnosticsProbe.Message); health=$healthUrl"
                $passReason = "$diagnosticsPassReason; window pid=$windowProcessId"
                if ($windowTitle) {
                    $passReason = "$passReason; window title='$windowTitle'"
                }

                $rendererEvidence = Test-RendererDomEvidence `
                    -RemoteDebuggingPort $remoteDebuggingPort `
                    -BackendUrl $backendUrl `
                    -ExpectedDataDir $dataDir `
                    -DesktopApiToken $desktopApiToken `
                    -RunRoot $runRoot `
                    -TimeoutSeconds ([Math]::Min(30, [Math]::Max(5, $TimeoutSeconds)))
                $rendererEvidenceStatus = $rendererEvidence.Status
                $rendererEvidenceMessage = $rendererEvidence.Message
                if ($rendererEvidence.PSObject.Properties.Name -contains "NaturalLanguageStatus" -and $rendererEvidence.NaturalLanguageStatus) {
                    $naturalLanguageEvidenceStatus = [string]$rendererEvidence.NaturalLanguageStatus
                    $naturalLanguageEvidenceMessage = [string]$rendererEvidence.NaturalLanguageMessage
                }
                if ($rendererEvidence.Ok) {
                    Write-SmokeStatus "pass" "portable renderer DOM read-only task evidence passed: $rendererEvidenceMessage" ([ConsoleColor]::Green)
                    $passReason = "$passReason; renderer DOM evidence=passed"
                    if ($naturalLanguageEvidenceStatus -eq "pass") {
                        Write-SmokeStatus "pass" "portable renderer DOM natural-language read-only task evidence passed: $naturalLanguageEvidenceMessage" ([ConsoleColor]::Green)
                        $passReason = "$passReason; natural-language renderer DOM evidence=passed"
                    }
                    elseif ($naturalLanguageEvidenceStatus -ne "not_attempted") {
                        Write-SmokeStatus $naturalLanguageEvidenceStatus "portable renderer DOM natural-language read-only task evidence unavailable: $naturalLanguageEvidenceMessage; read-only entry evidence remains valid but must not be counted as natural-language task evidence" ([ConsoleColor]::Yellow)
                        $passReason = "$passReason; natural-language renderer DOM evidence=$naturalLanguageEvidenceStatus"
                    }
                }
                elseif ($rendererEvidence.Status -eq "fail") {
                    Exit-SmokeFailed "portable renderer DOM read-only task evidence failed: $rendererEvidenceMessage"
                }
                else {
                    Write-SmokeStatus "unsupported" "portable renderer DOM read-only task evidence unavailable: $rendererEvidenceMessage; launcher/window/backend diagnostics pass remains limited and must not be counted as GUI-task automation evidence" ([ConsoleColor]::Yellow)
                    if (-not $AllowBackendOnlyPass) {
                        Exit-SmokeUnsupported "renderer DOM evidence unavailable in strict portable smoke; rerun with -AllowBackendOnlyPass only for legacy launcher/window/backend diagnostics evidence"
                    }
                    $passReason = "$passReason; renderer DOM evidence=unsupported; backend-only pass explicitly allowed"
                }
                break
            }
            elseif ($diagnosticsProbe.Ok) {
                if (-not $diagnosticsObserved) {
                    $diagnosticsObserved = $true
                    $diagnosticsPassReason = "$($diagnosticsProbe.Message); health=$healthUrl"
                    Write-SmokeStatus "info" "backend read-only diagnostics passed; waiting for portable window handle" ([ConsoleColor]::Cyan)
                }
                $lastProbe = "read-only diagnostics passed; no portable window handle yet"
            }
            else {
                $lastProbe = "backend health ok; $lastDiagnosticsProbe"
            }
        }
        else {
            $lastProbe = "no health response yet"
        }
        if (-not $windowObserved) {
            $lastProbe = "$lastProbe; no window handle yet"
        }

        if ($process.HasExited -and $launchedIds.Count -le 1) {
            $lastProbe = "launcher exited with code $($process.ExitCode) before read-only diagnostics passed"
            break
        }

        Start-Sleep -Milliseconds 500
    }
}
catch {
    $fatalError = $_.Exception.Message
    $lastProbe = "smoke script error: $fatalError"
}
finally {
    foreach ($key in $envVars.Keys) {
        $envPath = "Env:\$key"
        if ($previousEnv.ContainsKey($key) -and $null -ne $previousEnv[$key]) {
            Set-Item -Path $envPath -Value ([string]$previousEnv[$key])
        }
        else {
            Remove-Item -Path $envPath -ErrorAction SilentlyContinue
        }
    }

    if ($started) {
        Stop-LaunchedProcesses `
            -Process $process `
            -LauncherPath $launcherPath `
            -BackendPath $backendPath `
            -BaselineLauncherIds $baselineLauncherIds `
            -BaselineBackendIds $baselineBackendIds
    }
}

if ($fatalError) {
    Exit-SmokeFailed "portable first-screen/read-only diagnostics smoke errored before readiness was proven (last probe: $lastProbe; diagnostics probe: $lastDiagnosticsProbe; logs: $script:SmokeStatusLog ; $stdoutLog ; $stderrLog)"
}

if ($passed) {
    Write-SmokeStatus "pass" "portable first-screen/read-only diagnostics smoke passed: $passReason" ([ConsoleColor]::Green)
    if ($rendererEvidenceStatus -ne "pass") {
        Write-SmokeStatus "info" "renderer DOM evidence status: $rendererEvidenceStatus; $rendererEvidenceMessage" ([ConsoleColor]::Cyan)
    }
    Write-SmokeStatus "info" "logs: $stdoutLog ; $stderrLog" ([ConsoleColor]::Cyan)
    if ($RemoveTempOnPass) {
        Remove-Item -LiteralPath $runRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

Exit-SmokeFailed "portable first-screen/read-only diagnostics smoke did not prove both a visible portable window and backend diagnostics within $TimeoutSeconds seconds (last probe: $lastProbe; diagnostics probe: $lastDiagnosticsProbe; logs: $stdoutLog ; $stderrLog)"
