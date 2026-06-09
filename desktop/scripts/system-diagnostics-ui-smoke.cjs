const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { chromium } = require("@playwright/test");

const desktopRoot = path.resolve(__dirname, "..");
const previewHost = "127.0.0.1";
const backendOrigin = "http://127.0.0.1:8000";
const previewReadyTimeoutMs = 30_000;
const desktopViewport = { width: 1366, height: 768 };
const narrowViewport = { width: 390, height: 844 };
const overpromisingPathCopyPattern = /安全公开|放心公开|公开完整路径|可以直接公开|无需脱敏|不会包含隐私|随意分享/;
const positiveUpdaterCopyPattern = /下载更新|自动安装更新|立即更新|检查在线更新|联网查询更新|开始下载|点击安装/;
const onlineUpdaterEndpointPattern =
  /\/(?:api\/)?(?:app\/|system\/)?updates?\b|\/api\/updater\b|\/api\/releases\/(?:check|latest)\b|\/update-check\b|\/auto-update\b/i;

const backendDiagnostics = {
  info: {
    memory_total: 16 * 1024 * 1024 * 1024,
    memory_available: 9 * 1024 * 1024 * 1024
  },
  disks: [
    {
      device: "C:",
      mountpoint: "C:\\",
      fstype: "NTFS",
      usage: {
        total: 512 * 1024 * 1024 * 1024,
        used: 220 * 1024 * 1024 * 1024,
        free: 292 * 1024 * 1024 * 1024,
        percent: 43
      }
    }
  ],
  network: {},
  battery: null,
  top_processes: [
    {
      pid: 4242,
      name: "Lengrvis.exe",
      cpu_percent: 2.1,
      memory_bytes: 268435456,
      status: "running"
    }
  ],
  startup_items: [],
  suggestions: ["No critical system issue detected from read-only diagnostics."],
  diagnostic_scope: "local_only",
  product: {
    name: "Lengrvis",
    version: "0.1.0-smoke"
  },
  update_channel: {
    configured: false,
    status: "not_configured",
    label: "未配置在线更新通道",
    detail: "当前未配置在线更新通道，只显示本机版本与本地发布说明。",
    check_action: "refresh_local_status",
    offline_only: true,
    user_action_label: "刷新本机状态",
    release_notes: {
      available: true,
      label: "本地发布说明",
      detail: "打开随安装包提供的说明文件；本页不会联网检查更新。",
      path: "C:\\Program Files\\Lengrvis\\README.md",
      source: "local_file"
    },
    next_steps: [
      "确认是否有新版：查看本地发布说明或新的安装包说明。",
      "遇到故障：导出诊断包，再打开日志位置排查。"
    ]
  },
  local_paths: {
    data_dir: "C:\\Users\\Smoke\\AppData\\Local\\Lengrvis",
    database: "C:\\Users\\Smoke\\AppData\\Local\\Lengrvis\\lengrvis.db",
    log_dirs: ["C:\\Users\\Smoke\\AppData\\Local\\Lengrvis\\logs"]
  },
  support_package_redaction: {
    scope: "local_only",
    intended_audience: "trusted_support",
    public_safe: false,
    review_before_external_sharing: true,
    external_sharing_allowed: false,
    fail_closed: true,
    current_response: {
      // Deliberately inconsistent: the UI must fail closed when safety fields disagree.
      public_safe: true,
      contains_local_paths: true,
      external_review_required: false
    },
    external_review: {
      status: "manual_review_required",
      // Deliberately inconsistent: the UI must fail closed while public_safe is false.
      required_before_external_sharing: false,
      public_safe: false,
      external_sharing_allowed: false,
      fail_closed: true,
      checklist: [
        { id: "scope_and_audience", required: true },
        { id: "raw_logs_and_artifacts", required: true },
        { id: "local_paths", required: true },
        { id: "secrets_and_identifiers", required: true },
        { id: "task_content", required: true },
        { id: "external_sharing_decision", required: true }
      ]
    },
    guidance: "外发前需要人工复核。"
  },
  diagnostic_hints: ["local_only"]
};

const backendSettings = {
  base_url: backendOrigin,
  allowed_directories: ["C:\\Users\\Smoke\\Documents"],
  mode: "efficiency",
  allow_cloud_context: false,
  allow_file_content_upload: false
};

async function main() {
  assertBuiltRendererExists();
  assertSystemInfoSourceDoesNotOverclaimExternalSharing();

  const previewPort = Number(process.env.LENGRVIS_SYSTEM_DIAGNOSTICS_UI_PORT) || await getFreePort();
  const previewUrl = `http://${previewHost}:${previewPort}`;
  const preview = startPreview(previewPort);
  let browser;

  try {
    await waitForPreview(previewUrl);
    browser = await chromium.launch();
    const desktopCounters = createNetworkCounters();
    const desktopPage = await openDiagnosticsPage(browser, previewUrl, desktopViewport, desktopCounters);
    await assertUpdateCardIsLocalOnly(desktopPage, desktopCounters);
    await assertDiagnosticExportEntry(desktopPage, desktopCounters);
    await assertLogPathCopyDoesNotOverpromise(desktopPage);
    await assertDiagnosticsLayout(desktopPage, "desktop diagnostics view");
    assertNoOnlineUpdaterRequests(desktopCounters);
    await desktopPage.close();

    const narrowCounters = createNetworkCounters();
    const narrowPage = await openDiagnosticsPage(browser, previewUrl, narrowViewport, narrowCounters);
    await assertUpdateCardIsLocalOnly(narrowPage, narrowCounters);
    await assertDiagnosticExportEntry(narrowPage, narrowCounters);
    await assertLogPathCopyDoesNotOverpromise(narrowPage);
    await assertDiagnosticsLayout(narrowPage, "narrow diagnostics view");
    assertNoOnlineUpdaterRequests(narrowCounters);
    await narrowPage.close();
  } finally {
    if (browser) await browser.close();
    await stopProcess(preview);
  }

  console.log("desktop system diagnostics UI smoke passed");
}

function createNetworkCounters() {
  return {
    diagnosticExportRequests: 0,
    diagnosticExportAnyRequests: 0,
    diagnosticExportMethods: [],
    postRequests: [],
    systemDiagnosticsRequests: 0,
    systemInfoRequests: 0,
    onlineUpdateRequests: 0,
    onlineUpdateUrls: []
  };
}

async function openDiagnosticsPage(browser, previewUrl, viewport, counters) {
  const page = await browser.newPage({ viewport });
  page.on("pageerror", (error) => {
    throw error;
  });
  await installApiMocks(page, counters);
  await page.goto(`${previewUrl}/?view=computer`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForSelector("[data-testid='system-update-card']", { timeout: 30_000 });
  return page;
}

function assertBuiltRendererExists() {
  const indexPath = path.join(desktopRoot, "dist", "renderer", "index.html");
  assert.ok(
    fs.existsSync(indexPath),
    "renderer preview build is missing; run `npm run build:renderer` before system diagnostics UI smoke"
  );
}

function assertSystemInfoSourceDoesNotOverclaimExternalSharing() {
  const source = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "components", "SystemInfoPanel.tsx"), "utf8");
  assert.doesNotMatch(
    source,
    /可外发|外发复核已通过|复核字段/,
    "SystemInfoPanel source must not present diagnostics review fields as external-sharing approval"
  );
}

function startPreview(port) {
  const viteBin = path.join(desktopRoot, "node_modules", "vite", "bin", "vite.js");
  const child = spawn(process.execPath, [viteBin, "preview", "--host", previewHost, "--port", String(port), "--strictPort"], {
    cwd: desktopRoot,
    stdio: ["ignore", "pipe", "pipe"]
  });
  child.stdout.on("data", (data) => process.stdout.write(data));
  child.stderr.on("data", (data) => process.stderr.write(data));
  return child;
}

async function waitForPreview(previewUrl) {
  const deadline = Date.now() + previewReadyTimeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(previewUrl);
      if (response.ok) return;
    } catch {
      // Keep polling until Vite preview is ready.
    }
    await delay(300);
  }
  throw new Error(`Vite preview did not start in time at ${previewUrl}`);
}

async function assertUpdateCardIsLocalOnly(page, counters) {
  const card = page.getByTestId("system-update-card");
  const cardText = await card.innerText();
  assert.match(cardText, /版本与更新/, "system info panel should expose the version section");
  assert.match(cardText, /未配置在线更新通道/, "version section should name that online updates are not configured");
  assert.match(cardText, /只显示本机版本与本地发布说明|只会刷新本机版本和服务信息/, "version section should explain the local-only refresh");
  assert.match(cardText, /刷新本机状态/, "version action should be framed as local status refresh");
  assert.match(cardText, /本地发布说明/, "version section should expose packaged local release notes");
  assertNoPositiveUpdaterCopy(cardText, "version card must not look like an online updater");

  const updateDetail = await page.getByTestId("system-update-detail").innerText();
  assert.match(updateDetail, /本机|本地/, "version detail should frame refresh as a local-only status check");
  assertNoPositiveUpdaterCopy(updateDetail, "version detail must not imply online update behavior");

  const channelLabel = await page.getByTestId("system-update-channel-label").innerText();
  assert.match(channelLabel, /未配置在线更新通道/, "channel label should stay explicit about the missing online updater channel");
  const releaseNotesLabel = await page.getByTestId("system-release-notes-label").innerText();
  assert.match(releaseNotesLabel, /本地发布说明/, "release notes label should point to packaged local notes");

  const refreshButton = page.getByTestId("system-update-refresh-button");
  assert.equal(await refreshButton.isEnabled(), true, "local status refresh button should be enabled");
  assertNoPositiveUpdaterCopy(await refreshButton.innerText(), "refresh action should not be worded as an updater");
  assert.equal(await page.getByTestId("system-release-notes-button").isEnabled(), true, "local release notes entry should be available");

  const nextStepsText = await page.getByTestId("system-update-next-steps").innerText();
  assert.match(nextStepsText, /确认是否有新版：查看本地发布说明/, "next steps should route version questions to local release notes");
  assert.match(nextStepsText, /遇到故障：导出诊断包，再打开日志位置排查/, "next steps should route support cases to diagnostics and logs");
  assertNoPositiveUpdaterCopy(nextStepsText, "next steps must not imply online update capability");

  const systemInfoRequestsBefore = counters.systemInfoRequests;
  const diagnosticsRequestsBefore = counters.systemDiagnosticsRequests;
  await refreshButton.click();
  await waitForCounter(() => counters.systemInfoRequests > systemInfoRequestsBefore, "system info refresh request");
  await waitForCounter(() => counters.systemDiagnosticsRequests > diagnosticsRequestsBefore, "system diagnostics refresh request");
  await page.waitForFunction(() => {
    const text = document.querySelector("[data-testid='system-update-card']")?.textContent ?? "";
    return text.includes("已刷新当前安装版本和后端版本") || text.includes("未配置在线更新通道");
  }, null, { timeout: 10_000 });

  const refreshedText = await card.innerText();
  assert.match(refreshedText, /已刷新当前安装版本和后端版本|未配置在线更新通道/, "refresh should stay on local installed-version status");
  assert.equal(counters.onlineUpdateRequests, 0, "refreshing local status must not call updater endpoints");
}

async function assertDiagnosticExportEntry(page, counters) {
  const exportCard = page.getByTestId("diagnostic-export-card");
  const exportText = await exportCard.innerText();
  assert.match(exportText, /遇到问题时导出诊断包/, "system info panel should expose diagnostics export entry");
  assert.match(exportText, /脱敏路径/, "diagnostics export copy should explain that paths are redacted where possible");
  assert.match(exportText, /只有点击.+导出诊断包.+才会生成文件/, "diagnostics export copy should say export requires a manual click");
  assert.match(exportText, /不会自动导出或发送/, "diagnostics export copy should say the app will not auto-export or auto-send");
  assert.match(exportText, /本机范围摘要/, "diagnostics export copy should frame the package as local-scope diagnostics");
  assert.match(exportText, /版本、服务状态、网络接口/, "diagnostics export copy should describe support bundle contents");
  assert.match(exportText, /不包含你的文档正文、文件内容或密钥/, "diagnostics export copy should make privacy boundary visible");
  assert.match(exportText, /普通页面字段仍可能显示本机路径/, "diagnostics export copy should warn that regular UI fields may still show local paths");
  assert.doesNotMatch(
    exportText,
    /public-safe|public_safe|复核字段/,
    "diagnostics export card must not show raw safety metadata or field-oriented copy in user-visible text"
  );
  assert.match(exportText, /不可公开分享/, "diagnostics export card should show the Chinese unsafe-to-share boundary");
  assert.match(exportText, /外发前需人工复核/, "diagnostics export card should show the Chinese manual-review boundary");
  assert.match(exportText, /导出需手动点击/, "diagnostics export card should show that export requires an explicit manual click");
  assertNoOverpromisingPathCopy(exportText, "diagnostics export copy must not imply the full path is safe to publish");
  const review = page.getByTestId("diagnostic-export-review");
  const reviewText = await review.innerText();
  assert.equal(await review.getAttribute("data-public-safe"), "false", "raw package public_safe should remain testable");
  assert.equal(await review.getAttribute("data-external-sharing-safe"), "false", "inconsistent safety fields must fail closed");
  assert.equal(await review.getAttribute("data-safety-signals-consistent"), "false", "inconsistent safety fields should be surfaced for tests");
  assert.equal(await review.getAttribute("data-review-required"), "true", "unsafe packages should require external review");
  assert.match(
    await review.getAttribute("data-blocking-reasons"),
    /safety_signals_inconsistent_or_incomplete/,
    "inconsistent safety fields should expose a machine-testable blocking reason"
  );
  assert.match(
    await review.getAttribute("data-blocking-reasons"),
    /package_fail_closed/,
    "diagnostics export should honor backend fail_closed safety metadata"
  );
  assert.match(
    await review.getAttribute("data-blocking-reasons"),
    /external_review_external_sharing_allowed_false/,
    "diagnostics export should honor external_sharing_allowed=false"
  );
  assert.doesNotMatch(
    reviewText,
    /public-safe|public_safe|复核字段/,
    "diagnostics export review must explain safety in beginner-facing copy, not raw metadata or field-oriented wording"
  );
  assert.match(reviewText, /不可公开分享/, "diagnostics export should use beginner-facing unsafe-to-share copy");
  assert.match(reviewText, /外发前需人工复核/, "diagnostics export should require manual review before external sharing");
  assert.match(reviewText, /导出需手动点击/, "diagnostics export should make the explicit manual-click boundary visible");
  assert.match(reviewText, /安全状态不完整，按禁止外发处理/, "diagnostics export should explain fail-closed handling for inconsistent safety signals");
  assert.doesNotMatch(reviewText, /可外发/, "diagnostics export must not display external-share-safe copy when fields disagree");
  assert.doesNotMatch(reviewText, /外发复核已通过/, "diagnostics export must not present review metadata as sharing approval");
  assert.match(reviewText, /6 项复核清单/, "diagnostics export should surface the external review checklist count");
  assert.equal(counters.diagnosticExportRequests, 0, "diagnostics package should not be exported before user action");
  assert.equal(counters.diagnosticExportAnyRequests, 0, "diagnostics export endpoint should not be requested before user action");
  assert.deepEqual(counters.postRequests, [], "system diagnostics view should not POST to the backend before the explicit export action");

  await page.getByTestId("diagnostic-export-button").click();
  await waitForCounter(() => counters.diagnosticExportRequests === 1, "diagnostics export request");
  assert.deepEqual(counters.diagnosticExportMethods, ["POST"], "diagnostics export should be triggered exactly once and only with POST");
  assert.deepEqual(counters.postRequests, ["/api/system/diagnostics/export"], "the first backend POST should be the user-triggered diagnostics export");

  const status = page.getByTestId("diagnostic-export-status");
  await status.waitFor({ timeout: 10_000 });
  const statusText = await status.innerText();
  assert.match(statusText, /诊断包已生成/, "export action should report the generated diagnostics package");
  assert.match(statusText, /手动点击触发/, "success status should say the export was triggered by the user");
  assert.match(statusText, /不会自动发送诊断包/, "success status should say the app does not automatically send diagnostics");
  assert.match(statusText, /不要把完整路径当作可公开信息/, "success status should not imply that full local paths are public-safe");
  assert.match(statusText, /普通页面仍可能显示本机路径/, "success status should preserve the local-path caveat");
  assert.match(statusText, /外发前需人工复核/, "success status should keep the manual external-review boundary visible");
  assert.match(statusText, /当前不可公开分享/, "success status should explicitly avoid public-safe claims in beginner-facing copy");
  assert.doesNotMatch(statusText, /public-safe|public_safe/, "success status must not expose raw public-safety contract terms");
  assertNoOverpromisingPathCopy(statusText, "success status must not overpromise local path safety");

  const pathText = await page.getByTestId("diagnostic-export-path").innerText();
  assert.match(pathText, /本机保存位置/, "success state should label the local save location");
  assert.match(pathText, /只显示文件名或压缩路径/, "local path should be minimized by default");
  assert.match(pathText, /打开所在位置/, "local path copy should point users to the reveal button instead of exposing the full path");
  assert.match(pathText, /不要公开完整路径/, "local path copy should warn against publicly sharing the full path");
  assert.match(pathText, /lengrvis-diagnostics-smoke\.json/);
  assert.doesNotMatch(pathText, /C:\\Users\\Smoke/, "diagnostics path display must not expose the full local account path by default");
  assertNoOverpromisingPathCopy(pathText, "local path copy must not imply the full path is safe to publish");
}

async function assertLogPathCopyDoesNotOverpromise(page) {
  const logText = await page.evaluate(() => {
    const section = [...document.querySelectorAll(".system-section")].find(
      (element) => element.querySelector(".system-section__head strong")?.textContent?.trim() === "日志位置"
    );
    return section?.innerText ?? "";
  });
  assert.notEqual(logText, "", "system diagnostics view should render the log path section");
  assert.match(logText, /日志位置/, "system diagnostics view should expose the log path section");
  assert.match(logText, /排查问题|刷新后会显示日志目录/, "log path copy should frame logs as troubleshooting context");
  assert.match(logText, /C:\\Users\\Smoke\\AppData\\Local\\Lengrvis\\logs/, "log path section should show the mocked local log directory");
  assertNoOverpromisingPathCopy(logText, "log path copy must not imply full local paths are safe to publish");
}

async function assertDiagnosticsLayout(page, label) {
  const violations = await page.evaluate(() => {
    const tolerance = 4;
    const viewportWidth = document.documentElement.clientWidth;
    const failures = [];
    const documentOverflow = Math.ceil(document.documentElement.scrollWidth - viewportWidth);
    if (documentOverflow > tolerance) {
      failures.push(`document has ${documentOverflow}px horizontal overflow`);
    }

    const layoutSelectors = [
      ["system update card", "[data-testid='system-update-card']"],
      ["diagnostics export card", "[data-testid='diagnostic-export-card']"],
      ["update next steps", "[data-testid='system-update-next-steps']"],
      ["system grid", ".system-grid"],
      ["log path list", ".system-path-list"]
    ];
    for (const [name, selector] of layoutSelectors) {
      const element = document.querySelector(selector);
      if (!element) {
        failures.push(`${name} is missing`);
        continue;
      }
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        failures.push(`${name} is not visible`);
      }
      if (rect.left < -tolerance || rect.right > viewportWidth + tolerance) {
        failures.push(`${name} extends beyond viewport (${Math.round(rect.left)}..${Math.round(rect.right)} of ${viewportWidth})`);
      }
    }

    const textFitSelectors = [
      ["update detail", "[data-testid='system-update-detail']"],
      ["update facts", ".system-update-card__facts"],
      ["diagnostics export copy", "[data-testid='diagnostic-export-card'] .diagnostic-export__body span"],
      ["diagnostics export review", "[data-testid='diagnostic-export-review'] span"],
      ["diagnostics export status", "[data-testid='diagnostic-export-status'] span"],
      ["diagnostics saved path", "[data-testid='diagnostic-export-path']"],
      ["log path", ".system-path-row code"]
    ];
    for (const [name, selector] of textFitSelectors) {
      for (const element of document.querySelectorAll(selector)) {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const overflow = Math.ceil(element.scrollWidth - element.clientWidth);
        if (overflow > tolerance) {
          failures.push(`${name} has ${overflow}px horizontal text overflow`);
        }
      }
    }
    return failures;
  });

  assert.deepEqual(violations, [], `${label} should not horizontally overflow or clip diagnostics copy`);
}

function assertNoOnlineUpdaterRequests(counters) {
  assert.deepEqual(
    counters.onlineUpdateUrls,
    [],
    `system diagnostics UI must not call online updater endpoints: ${counters.onlineUpdateUrls.join(", ")}`
  );
}

function assertNoPositiveUpdaterCopy(text, message) {
  const copyWithoutSafetyStatements = text
    .replace(/不会联网查询、下载或自动安装更新/g, "")
    .replace(/不会下载或安装更新/g, "");
  assert.doesNotMatch(copyWithoutSafetyStatements, positiveUpdaterCopyPattern, message);
}

function assertNoOverpromisingPathCopy(text, message) {
  const copyWithoutSafetyStatements = text
    .replace(/不建议公开完整路径/g, "")
    .replace(/不要公开完整路径/g, "")
    .replace(/不要把完整路径当作可公开信息/g, "");
  assert.doesNotMatch(copyWithoutSafetyStatements, overpromisingPathCopyPattern, message);
}

async function installApiMocks(page, counters) {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method().toUpperCase();
    if (method === "POST" && url.origin === backendOrigin) {
      counters.postRequests.push(url.pathname);
    }
    if (onlineUpdaterEndpointPattern.test(url.pathname)) {
      counters.onlineUpdateRequests += 1;
      counters.onlineUpdateUrls.push(`${method} ${url.href}`);
    }
    if (url.origin !== backendOrigin) {
      await route.continue();
      return;
    }

    if (onlineUpdaterEndpointPattern.test(url.pathname)) {
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: "online updater endpoints must not be used by this smoke" })
      });
    }

    const json = (body) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body)
    });

    if (url.pathname === "/api/health") return json({ status: "ok" });
    if (url.pathname === "/api/chat/messages") return json([]);
    if (url.pathname === "/api/runs") return json([]);
    if (url.pathname === "/api/tasks") return json([]);
    if (url.pathname === "/api/current-plan") return json({});
    if (url.pathname === "/api/approvals/pending") return json([]);
    if (url.pathname === "/api/settings") return json(backendSettings);
    if (url.pathname === "/api/settings/llm/health") return json({ active: { available: true, provider: "smoke", model: "smoke" }, retry: {} });
    if (url.pathname === "/api/settings/llm/cost-summary") return json({ by_model: [] });
    if (url.pathname === "/api/context/usage") return json({});
    if (url.pathname === "/api/audit" || url.pathname === "/api/audit/logs") return json([]);
    if (url.pathname === "/api/chat/proactive-suggestions") return json([]);
    if (url.pathname === "/api/browser/sessions") return json({ ok: true, sessions: [] });
    if (url.pathname === "/api/system/info") {
      counters.systemInfoRequests += 1;
      return json({ system: "Windows", platform: "win32", machine: "x64" });
    }
    if (url.pathname === "/api/system/diagnostics") {
      counters.systemDiagnosticsRequests += 1;
      return json(backendDiagnostics);
    }
    if (url.pathname === "/api/system/processes") return json({ processes: backendDiagnostics.top_processes, count: backendDiagnostics.top_processes.length });
    if (url.pathname === "/api/system/startup-items") return json({ startup_items: [], count: 0 });
    if (url.pathname === "/api/apps") return json({ apps: [] });
    if (url.pathname === "/api/system/diagnostics/export") {
      counters.diagnosticExportAnyRequests += 1;
      counters.diagnosticExportMethods.push(method);
      if (method === "POST") {
        counters.diagnosticExportRequests += 1;
        return json({
          ok: true,
          path: "C:\\Users\\Smoke\\AppData\\Local\\Lengrvis\\diagnostics\\lengrvis-diagnostics-smoke.json",
          filename: "lengrvis-diagnostics-smoke.json",
          created_at: "2026-06-08T00:00:00.000Z",
          bytes: 4096
        });
      }
      return route.fulfill({
        status: 405,
        contentType: "application/json",
        body: JSON.stringify({ error: `diagnostics export requires POST, received ${method}` })
      });
    }

    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: `unmocked system diagnostics UI smoke endpoint: ${method} ${url.pathname}` })
    });
  });
}

async function waitForCounter(predicate, label) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, previewHost, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

async function stopProcess(child) {
  if (!child || child.killed) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  child.kill();
  await Promise.race([exited, delay(3_000)]);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
