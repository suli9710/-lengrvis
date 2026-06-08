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

  const previewPort = Number(process.env.LENGRVIS_SYSTEM_DIAGNOSTICS_UI_PORT) || await getFreePort();
  const previewUrl = `http://${previewHost}:${previewPort}`;
  const preview = startPreview(previewPort);
  let browser;

  try {
    await waitForPreview(previewUrl);
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    page.on("pageerror", (error) => {
      throw error;
    });

    const counters = {
      diagnosticExportRequests: 0,
      systemDiagnosticsRequests: 0,
      systemInfoRequests: 0,
      onlineUpdateRequests: 0
    };
    await installApiMocks(page, counters);
    await page.goto(`${previewUrl}/?view=computer`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForSelector("[data-testid='system-update-card']", { timeout: 30_000 });

    await assertUpdateCardIsLocalOnly(page, counters);
    await assertDiagnosticExportEntry(page, counters);
    assert.equal(counters.onlineUpdateRequests, 0, "system diagnostics UI must not call online updater endpoints");
    await page.close();
  } finally {
    if (browser) await browser.close();
    await stopProcess(preview);
  }

  console.log("desktop system diagnostics UI smoke passed");
}

function assertBuiltRendererExists() {
  const indexPath = path.join(desktopRoot, "dist", "renderer", "index.html");
  assert.ok(
    fs.existsSync(indexPath),
    "renderer preview build is missing; run `npm run build:renderer` before system diagnostics UI smoke"
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
  assert.doesNotMatch(cardText, /下载更新|自动安装更新|立即更新/, "version card must not look like an online updater");

  const refreshButton = page.getByTestId("system-update-refresh-button");
  assert.equal(await refreshButton.isEnabled(), true, "local status refresh button should be enabled");
  assert.equal(await page.getByTestId("system-release-notes-button").isEnabled(), true, "local release notes entry should be available");

  const nextStepsText = await page.getByTestId("system-update-next-steps").innerText();
  assert.match(nextStepsText, /确认是否有新版：查看本地发布说明/, "next steps should route version questions to local release notes");
  assert.match(nextStepsText, /遇到故障：导出诊断包，再打开日志位置排查/, "next steps should route support cases to diagnostics and logs");
  assert.doesNotMatch(nextStepsText, /下载更新|自动安装更新|立即更新/, "next steps must not imply online update capability");

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
  assert.match(exportText, /本机范围摘要/, "diagnostics export copy should frame the package as local-scope diagnostics");
  assert.match(exportText, /版本、服务状态、网络接口/, "diagnostics export copy should describe support bundle contents");
  assert.match(exportText, /不包含你的文档正文、文件内容或密钥/, "diagnostics export copy should make privacy boundary visible");
  assert.doesNotMatch(exportText, /安全公开|放心公开/, "diagnostics export copy must not imply the full path is safe to publish");
  assert.equal(counters.diagnosticExportRequests, 0, "diagnostics package should not be exported before user action");

  await page.getByTestId("diagnostic-export-button").click();
  await waitForCounter(() => counters.diagnosticExportRequests === 1, "diagnostics export request");

  const status = page.getByTestId("diagnostic-export-status");
  await status.waitFor({ timeout: 10_000 });
  const statusText = await status.innerText();
  assert.match(statusText, /诊断包已生成/, "export action should report the generated diagnostics package");
  assert.match(statusText, /不要把完整路径当作可公开信息/, "success status should not imply that full local paths are public-safe");

  const pathText = await page.getByTestId("diagnostic-export-path").innerText();
  assert.match(pathText, /本机保存位置/, "success state should label the local save location");
  assert.match(pathText, /仅用于在这台电脑上打开/, "local path should be framed as a same-machine convenience");
  assert.match(pathText, /不建议公开完整路径/, "local path copy should warn against publicly sharing the full path");
  assert.match(pathText, /lengrvis-diagnostics-smoke\.json/);
  assert.doesNotMatch(pathText, /安全公开|放心公开/, "local path copy must not imply the full path is safe to publish");
}

async function installApiMocks(page, counters) {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== backendOrigin) {
      await route.continue();
      return;
    }

    const method = request.method().toUpperCase();
    if (/\/(?:api\/)?(?:app\/|system\/)?updates?\b|\/api\/updater\b/i.test(url.pathname)) {
      counters.onlineUpdateRequests += 1;
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
    if (url.pathname === "/api/system/diagnostics/export" && method === "POST") {
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
