const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("@playwright/test");

const desktopRoot = path.resolve(__dirname, "..");
const previewHost = "127.0.0.1";
const backendOrigin = "http://127.0.0.1:8000";
const previewReadyTimeoutMs = 30_000;

const backendDiagnostics = {
  info: {
    memory_total: 16 * 1024 * 1024 * 1024,
    memory_available: 10 * 1024 * 1024 * 1024
  },
  disks: [
    {
      device: "C:",
      mountpoint: "C:\\",
      fstype: "NTFS",
      usage: {
        total: 512 * 1024 * 1024 * 1024,
        used: 180 * 1024 * 1024 * 1024,
        free: 332 * 1024 * 1024 * 1024,
        percent: 35
      }
    }
  ],
  network: {},
  battery: null,
  top_processes: [],
  startup_items: [],
  suggestions: ["No critical system issue detected from read-only diagnostics."],
  diagnostic_scope: "local_only",
  product: {
    name: "Lengrvis",
    version: "0.1.0-smoke"
  },
  local_paths: {
    log_dirs: ["C:\\Users\\Smoke\\AppData\\Local\\Lengrvis\\logs"]
  }
};

const backendSettings = {
  base_url: backendOrigin,
  allowed_directories: ["C:\\Users\\Smoke\\Documents"],
  mode: "efficiency",
  allow_cloud_context: false,
  allow_file_content_upload: false
};

const completedComputerTemplateTask = {
  id: "task-computer-template-smoke",
  user_goal: "检查电脑状态",
  final_summary: "只读电脑健康快照已生成，未修改系统设置。",
  status: "completed",
  created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
  updated_at: new Date().toISOString()
};

async function main() {
  runSourceAssertions();
  assertBuiltRendererExists();

  const previewPort = Number(process.env.LENGRVIS_FIRST_LAUNCH_PORT) || await getFreePort();
  const previewUrl = `http://${previewHost}:${previewPort}`;
  const preview = startPreview(previewPort);

  try {
    await waitForPreview(previewUrl);
    await assertFirstLaunchEntryWorks(previewUrl);
    await assertPromptQuickSkillUsesSelectedDraft(previewUrl);
    await assertBackendUnavailableState(previewUrl);
  } finally {
    await stopProcess(preview);
  }

  console.log("desktop first-launch smoke passed");
}

function runSourceAssertions() {
  const appSource = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "App.tsx"), "utf8");
  const officeSceneSource = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "features", "office", "OfficeScene.tsx"), "utf8");

  assert.match(
    officeSceneSource,
    /if \(skill\.kind === "prompt"\) \{\s*onQuickSkill\(skill\);/,
    "prompt quick skills should synchronously update the draft before the user can submit"
  );
  assert.match(
    appSource,
    /catch \(error\) \{\s*setSettings\(previousSettings\);\s*setMode\(previousMode\);\s*throw new Error\(readableError\(error,/,
    "thrown settings-save errors should restore the previous renderer settings and mode"
  );
  assert.match(
    appSource,
    /if \(!result\.ok\) \{\s*setSettings\(previousSettings\);\s*setMode\(previousMode\);/,
    "non-ok settings-save responses should restore the previous renderer settings and mode"
  );
}

function assertBuiltRendererExists() {
  const indexPath = path.join(desktopRoot, "dist", "renderer", "index.html");
  assert.ok(
    fs.existsSync(indexPath),
    "renderer preview build is missing; run `npm run build:renderer` before first-launch smoke"
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

async function assertFirstLaunchEntryWorks(previewUrl) {
  const counters = {};
  const { context, page, profileDir } = await openDisposablePage();
  try {
    await installHealthyBackendMocks(page, counters, { tasks: [completedComputerTemplateTask] });
    await gotoFirstLaunch(page, previewUrl);

    await page.getByRole("heading", { name: /问问 Lengrvis/ }).waitFor({ timeout: 15_000 });
    await page.getByText(/可以这样开始/).first().waitFor({ timeout: 15_000 });

    const quickEntries = page.locator("button.office-quick-card");
    assert.ok(await quickEntries.count() >= 1, "first screen should expose at least one task entry button");

    const checkComputerEntry = page.locator("button.office-quick-card", { hasText: /检查电脑状态/ }).first();
    await checkComputerEntry.waitFor({ timeout: 15_000 });
    assert.equal(await checkComputerEntry.isEnabled(), true, "read-only computer check entry should be clickable");

    const entryText = await checkComputerEntry.innerText();
    assert.match(entryText, /只读/, "computer check entry should explain that it is read-only");
    assert.match(entryText, /不上云|本机状态/, "computer check entry should make the local/privacy boundary visible");
    assert.match(entryText, /无改动/, "computer check entry should explain that it does not modify the system");

    await assertComputerTemplateHomeEvidence(page, { hasRecentResult: true });

    await checkComputerEntry.click();
    await page.getByText(/正在进行只读电脑检查|只读检查启动中/).first().waitFor({ timeout: 1_500 }).catch(() => undefined);
    await page.getByText(/系统信息/).first().waitFor({ timeout: 15_000 });
    await page.getByText(/一键只读检查/).first().waitFor({ timeout: 15_000 });
    await page.getByText(/只读诊断，不改设置/).first().waitFor({ timeout: 15_000 });
    await page.getByText(/Lengrvis 服务：已连接/).first().waitFor({ timeout: 15_000 });

    assert.equal(counters.taskLaunchRequests ?? 0, 0, "read-only first-launch entry must not create a chat/run task");
    assert.ok(counters.systemInfoRequests >= 1, "read-only first-launch entry should refresh system info");
  } finally {
    await context.close();
    removeTempDir(profileDir);
  }
}

async function assertPromptQuickSkillUsesSelectedDraft(previewUrl) {
  const counters = {};
  const { context, page, profileDir } = await openDisposablePage();
  try {
    await installHealthyBackendMocks(page, counters);
    await gotoFirstLaunch(page, previewUrl);

    const commandInput = page.locator(".office-command-dock textarea");
    await commandInput.waitFor({ timeout: 15_000 });
    await commandInput.fill("previous stale draft");

    await page.getByTestId("office-template-clean-downloads").click();
    const selectedPrompt = await commandInput.inputValue();
    assert.match(
      selectedPrompt,
      /\u626b\u63cf\u6211\u7684\u4e0b\u8f7d\u76ee\u5f55/,
      "prompt quick skill should replace the draft synchronously"
    );
    assert.doesNotMatch(selectedPrompt, /previous stale draft/, "prompt quick skill should not leave the previous draft submit-ready");

    const sendButton = page.locator("button.command-footer__send").first();
    assert.equal(await sendButton.isEnabled(), true, "send should be enabled for the selected quick-skill prompt");
    await sendButton.click();

    await waitForCounter(() => (counters.taskLaunchRequests ?? 0) >= 1, "quick-skill task launch request");
    const launchPayload = counters.taskLaunchPayloads?.[0] ?? {};
    assert.equal(
      launchPayload.message,
      selectedPrompt,
      "immediate Send after a prompt quick-skill click should submit the selected prompt, not the stale draft"
    );
  } finally {
    await context.close();
    removeTempDir(profileDir);
  }
}

async function assertBackendUnavailableState(previewUrl) {
  const counters = {};
  const { context, page, profileDir } = await openDisposablePage();
  try {
    await installUnavailableBackendMocks(page, counters);
    await gotoFirstLaunch(page, previewUrl);

    const checkComputerEntry = page.locator("button.office-quick-card", { hasText: /检查电脑状态/ }).first();
    await checkComputerEntry.waitFor({ timeout: 15_000 });
    assert.equal(await checkComputerEntry.isVisible(), true, "first-launch task entry should stay visible while backend is unavailable");
    await assertComputerTemplateHomeEvidence(page, { hasRecentResult: false });

    const connectionReadiness = page.locator(".home-readiness-item", { hasText: /Lengrvis 连接/ }).first();
    await connectionReadiness.waitFor({ timeout: 15_000 });
    const readinessText = await connectionReadiness.innerText();
    assert.match(readinessText, /服务离线|先恢复连接/, "connection readiness should explain that the service is offline");
    assert.match(readinessText, /检查连接/, "connection readiness should expose an actionable retry/check control");

    const commandInput = page.locator(".office-command-dock textarea");
    await commandInput.fill("帮我检查这台电脑");
    const sendButton = page.getByRole("button", { name: /发送/ }).first();
    assert.equal(await sendButton.isEnabled(), true, "send should let users retry a stale/offline connection check");
    await sendButton.click();

    const commandStatus = page.locator("#office-command-status");
    await expectText(commandStatus, /服务还没连上|连接检查失败/, "command status should name the backend connection problem after retry");
    const statusText = await commandStatus.innerText();
    assert.match(statusText, /服务还没连上/, "command status should name the backend connection problem");
    assert.match(statusText, /输入内容已保留|可以稍后重试/, "command status should tell the user what to do next");

    assert.equal(counters.taskLaunchRequests ?? 0, 0, "offline first screen must not create chat/run task requests");
    assert.ok(counters.backendRequests >= 1, "offline smoke should exercise backend failure handling");
  } finally {
    await context.close();
    removeTempDir(profileDir);
  }
}

async function openDisposablePage() {
  const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "lengrvis-first-launch-"));
  const context = await chromium.launchPersistentContext(profileDir, {
    headless: true,
    viewport: { width: 1366, height: 768 }
  });
  const page = context.pages()[0] ?? await context.newPage();
  page.on("pageerror", (error) => {
    throw error;
  });
  return { context, page, profileDir };
}

async function gotoFirstLaunch(page, previewUrl) {
  await page.goto(previewUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForSelector("#root > *", { timeout: 30_000 });
}

async function assertComputerTemplateHomeEvidence(page, { hasRecentResult }) {
  const templateButton = page.getByTestId("office-template-check-computer");
  await templateButton.waitFor({ timeout: 15_000 });
  const templateText = await templateButton.innerText();
  assert.match(templateText, /产出/, "computer template should show its expected output on the first screen");
  assert.match(templateText, /健康状态|缺失依赖|下一步修复入口/, "computer template should name the visible result");
  assert.match(templateText, /只读|不上云|无改动/, "computer template should expose the local read-only boundary");

  const outcomeCard = page.getByTestId("home-outcome-computer");
  await outcomeCard.waitFor({ timeout: 15_000 });
  const outcomeText = await outcomeCard.innerText();
  if (hasRecentResult) {
    assert.match(outcomeText, /系统检查有最近记录/, "computer template should render a visible outcome when a recent result exists");
    assert.match(outcomeText, /下一步：查看电脑状态页/, "computer outcome should expose a next-step action");
    assert.match(outcomeText, /只读状态|不会改系统设置/, "computer outcome should preserve the local task boundary");

    const workspaceText = await page.getByTestId("task-workspace-card").innerText();
    assert.match(workspaceText, /系统只读/, "Task Workspace should bind the computer template to a read-only system tool");
    assert.match(workspaceText, /已完成|按任务类型启用/, "Task Workspace should show local task evidence without faking a new run");
  } else {
    assert.match(outcomeText, /等待只读快照/, "computer template should have a clear fallback while no result exists");
    assert.match(outcomeText, /可一键启动只读检查/, "computer fallback should tell the user the next safe action");
  }
}

async function expectText(locator, pattern, message, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  let lastText = "";
  while (Date.now() < deadline) {
    lastText = await locator.innerText().catch(() => "");
    if (pattern.test(lastText)) return;
    await delay(100);
  }
  assert.match(lastText, pattern, message);
}

async function waitForCounter(predicate, label, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

async function installHealthyBackendMocks(page, counters, options = {}) {
  const tasks = options.tasks ?? [];
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== backendOrigin) {
      await route.continue();
      return;
    }

    count(counters, "backendRequests");
    const method = request.method().toUpperCase();
    if (method === "POST" && (url.pathname === "/api/chat" || url.pathname === "/api/runs")) {
      count(counters, "taskLaunchRequests");
      recordTaskLaunchPayload(counters, request);
    }
    if (url.pathname === "/api/system/info") {
      count(counters, "systemInfoRequests");
    }

    const json = (body) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body)
    });

    if (url.pathname === "/api/health") return json({ status: "ok" });
    if (url.pathname === "/api/chat" && method === "POST") {
      return json({
        task_id: "task-quick-skill-smoke",
        status: "queued",
        message: "Task queued by first-launch smoke.",
        delegated: true,
        agent: "FileAgent"
      });
    }
    if (url.pathname === "/api/runs" && method === "POST") {
      return json({
        run_id: "run_quick_skill_smoke",
        engine: "os",
        phase: "queued"
      });
    }
    if (url.pathname === "/api/chat/messages") return json([]);
    if (url.pathname === "/api/runs") return json([]);
    if (url.pathname === "/api/tasks") return json(tasks);
    if (url.pathname === "/api/approvals/pending") return json([]);
    if (url.pathname === "/api/settings") return json(backendSettings);
    if (url.pathname === "/api/settings/llm/health") return json({ active: { available: true, provider: "smoke", model: "smoke" }, retry: {} });
    if (url.pathname === "/api/settings/llm/cost-summary") return json({ by_model: [] });
    if (url.pathname === "/api/context/usage") return json({});
    if (url.pathname === "/api/audit") return json([]);
    if (url.pathname === "/api/system/info") return json({ system: "Windows", platform: "win32", machine: "x64" });
    if (url.pathname === "/api/system/diagnostics") return json(backendDiagnostics);
    if (url.pathname === "/api/system/processes") return json({ processes: [], count: 0 });
    if (url.pathname === "/api/system/startup-items") return json({ startup_items: [], count: 0 });
    if (url.pathname === "/api/apps") return json({ apps: [] });
    if (url.pathname === "/api/chat/proactive-suggestions") return json([]);
    if (url.pathname === "/api/browser/sessions") return json({ ok: true, sessions: [] });

    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: `unmocked first-launch smoke endpoint: ${url.pathname}` })
    });
  });
}

function recordTaskLaunchPayload(counters, request) {
  let payload = {};
  try {
    payload = request.postDataJSON();
  } catch {
    payload = { raw: request.postData() ?? "" };
  }
  counters.taskLaunchPayloads = [...(counters.taskLaunchPayloads ?? []), payload];
}

async function installUnavailableBackendMocks(page, counters) {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== backendOrigin) {
      await route.continue();
      return;
    }

    count(counters, "backendRequests");
    if (request.method().toUpperCase() === "POST" && (url.pathname === "/api/chat" || url.pathname === "/api/runs")) {
      count(counters, "taskLaunchRequests");
    }
    await route.abort("failed");
  });
}

function count(counters, key) {
  counters[key] = (counters[key] ?? 0) + 1;
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

function removeTempDir(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    // Disposable browser profiles are best-effort cleanup on Windows.
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
