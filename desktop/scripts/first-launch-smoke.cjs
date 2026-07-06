const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("@playwright/test");
const { stopProcessTree } = require("./smoke-process-utils.cjs");

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
  final_summary: "系统诊断只读电脑健康快照已生成，未修改系统设置。",
  status: "completed",
  completion_evidence: {
    level: "completed_result",
    result_verified: true,
    result_artifacts: [
      { kind: "final_summary", label: "只读检查摘要", redacted: true, count: 1 }
    ],
    missing: [],
    signoff: false
  },
  result_quality: {
    state: "verified_result",
    label: "完成结果已核验",
    summary: "只读系统检查结果已经核验，可以作为完成记录。",
    result_verified: true,
    can_treat_as_done: true,
    needs_review: false,
    missing_checks: [],
    next_step: "查看电脑状态页。",
    signoff: false,
    redacted: true,
    privacy_note: "仅展示脱敏摘要。"
  },
  result_verified: true,
  created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
  updated_at: new Date().toISOString()
};

const unverifiedCompletedComputerTemplateTask = {
  id: "task-computer-template-unverified-smoke",
  user_goal: "检查电脑状态",
  final_summary: "系统诊断只读检查有进度记录，但结果还未核验。",
  status: "completed",
  completion_evidence: {
    level: "visible_progress",
    result_verified: false,
    result_artifacts: [
      { kind: "tool_result", label: "只读检查进度", redacted: true, count: 1 }
    ],
    missing: ["结果核验"],
    signoff: false
  },
  result_quality: {
    state: "visible_progress",
    label: "有进度，待核验",
    summary: "只读系统检查有进度记录，但还不能确认健康结论。",
    result_verified: false,
    can_treat_as_done: false,
    needs_review: true,
    missing_checks: ["结果核验"],
    next_step: "查看时间线或重新检查。",
    signoff: false,
    redacted: true,
    privacy_note: "仅展示脱敏摘要。"
  },
  result_verified: false,
  created_at: new Date(Date.now() - 4 * 60 * 1000).toISOString(),
  updated_at: new Date().toISOString()
};

const safeFailureComputerTemplateTask = {
  id: "task-computer-template-safe-failure-smoke",
  user_goal: "检查电脑状态",
  final_summary: "系统诊断安全停止，未生成完成结果，未修改系统设置。",
  status: "completed",
  completion_evidence: {
    level: "safe_failure",
    result_verified: false,
    result_artifacts: [
      { kind: "safe_stop", label: "安全停止记录", redacted: true, count: 1 }
    ],
    missing: ["完成结果"],
    signoff: false
  },
  result_quality: {
    state: "safe_failure",
    label: "安全停止，需处理",
    summary: "安全策略停止了任务，没有形成完成结果。",
    result_verified: false,
    can_treat_as_done: false,
    needs_review: true,
    missing_checks: ["完成结果"],
    next_step: "查看原因后重试。",
    signoff: false,
    redacted: true,
    privacy_note: "仅展示脱敏摘要。"
  },
  result_verified: false,
  created_at: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
  updated_at: new Date().toISOString()
};

const taskEvidenceOnlyComputerTemplateTask = {
  id: "task-computer-template-record-only-smoke",
  user_goal: "检查电脑状态",
  final_summary: "系统诊断任务已创建，但还没有完成结果记录。",
  status: "completed",
  completion_evidence: {
    level: "task_created",
    result_verified: false,
    result_artifacts: [
      { kind: "task_record", label: "任务记录", redacted: true, count: 1 }
    ],
    missing: ["完成结果", "结果核验"],
    signoff: false
  },
  result_quality: {
    state: "task_evidence_only",
    label: "仅有任务记录",
    summary: "任务只有提交或创建记录，不能当作完成结果。",
    result_verified: false,
    can_treat_as_done: false,
    needs_review: true,
    missing_checks: ["完成结果", "结果核验"],
    next_step: "核对结果或重新运行。",
    signoff: false,
    redacted: true,
    privacy_note: "仅展示脱敏摘要。"
  },
  result_verified: false,
  created_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
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
    await assertTaskResultQualityStatesStayActionable(previewUrl);
    await assertNaturalLanguageComputerCheckStartsReadOnlyRun(previewUrl);
    await assertPromptQuickSkillUsesSelectedDraft(previewUrl);
    await assertBackendUnavailableState(previewUrl);
  } finally {
    await stopProcess(preview);
  }

  console.log("desktop first-launch smoke passed");
}

function runSourceAssertions() {
  const appSource = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "App.tsx"), "utf8");
  const appViewModelSource = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "appViewModel.tsx"), "utf8");
  const zhSource = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "lib", "zh.ts"), "utf8");
  const officeSceneSource = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "features", "office", "OfficeScene.tsx"), "utf8");

  assert.match(
    officeSceneSource,
    /if \(skill\.kind === "prompt"\) \{\s*onQuickSkill\(skill\);/,
    "prompt quick skills should synchronously update the draft before the user can submit"
  );
  assert.match(
    appSource,
    /catch \(error\) \{\s*(?:\/\/[^\n]*\s*)?setSettings\(previousSettings\);\s*setMode\(previousMode\);\s*throw new Error\(readableError\(error,/,
    "thrown settings-save errors should restore the previous renderer settings and mode"
  );
  assert.match(
    appSource,
    /if \(!result\.ok\) \{\s*setSettings\(previousSettings\);\s*setMode\(previousMode\);/,
    "non-ok settings-save responses should restore the previous renderer settings and mode"
  );
  assert.match(
    appViewModelSource,
    /const sample = safeRealtimeBadMessageSample\(\);/,
    "malformed realtime messages must use a fixed safe summary instead of sampling raw payload text"
  );
  assert.match(
    appViewModelSource,
    /function safeRealtimeBadMessageSample\(\): string \{\s*return "原始内容已隐藏，避免显示本机路径、文件名、连接地址、提示词或凭据。";\s*\}/,
    "realtime bad-message handling must fail closed without exposing raw filenames or hidden prompt text"
  );
  assert.doesNotMatch(
    appViewModelSource,
    /containsRealtimeSensitiveDetail|safeRealtimeBadMessageSample\(status\.rawMessage\)|status\.rawMessage\.replace/,
    "malformed realtime handling must not inspect and echo arbitrary raw payload snippets"
  );
  assert.doesNotMatch(
    zhSource,
    /已保留原始内容/,
    "novice realtime notices must not say raw malformed realtime content is retained"
  );
  assert.doesNotMatch(
    zhSource,
    /最近原文预览/,
    "novice realtime notices must not label malformed payload snippets as raw previews"
  );
  assert.match(
    zhSource,
    /最近安全摘要/,
    "novice realtime notices should describe malformed payload snippets as safe summaries"
  );
  assert.doesNotMatch(
    officeSceneSource,
    /证据/,
    "office result-quality UI should use novice-facing result and record language instead of internal evidence jargon"
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
    const firstEntryText = await quickEntries.first().innerText();
    assert.match(firstEntryText, /检查电脑状态/, "first visible task template should be the safest no-input entry");
    assert.match(firstEntryText, /点发送开始|一句话可选/, "first task entry should explain how to start explicitly");
    assert.match(firstEntryText, /只读快照|只读取/, "first task entry should make the read-only preflight visible");
    assert.match(firstEntryText, /30 秒/, "first task entry should show a short first-run estimate");

    const checkComputerEntry = page.locator("button.office-quick-card", { hasText: /检查电脑状态/ }).first();
    await checkComputerEntry.waitFor({ timeout: 15_000 });
    assert.equal(await checkComputerEntry.isEnabled(), true, "read-only computer check entry should be clickable");

    await checkComputerEntry.hover();
    const expandedEntryText = await checkComputerEntry.innerText();
    assert.match(expandedEntryText, /只读/, "computer check entry should explain that it is read-only");
    assert.match(expandedEntryText, /不上云|本机状态/, "computer check entry should make the local/privacy boundary visible");
    assert.match(expandedEntryText, /无改动/, "computer check entry should explain that it does not modify the system");

    await assertComputerTemplateHomeEvidence(page, { hasRecentResult: true });
    await assertHomeTrustBoundary(page);

    const systemInfoRequestsBefore = counters.systemInfoRequests ?? 0;
    const commandInput = page.locator(".office-command-dock textarea");
    await checkComputerEntry.click();
    assert.equal(await commandInput.inputValue(), "帮我检查这台电脑", "computer template click should fill the command bar");
    assert.equal(counters.taskLaunchRequests ?? 0, 0, "template selection must not create a chat/run task");
    assert.equal(counters.systemInfoRequests ?? 0, systemInfoRequestsBefore, "template selection should not refresh system info yet");

    const wizardText = await page.getByTestId("office-template-wizard").innerText();
    assert.match(wizardText, /点击发送后启动只读电脑检查/, "computer wizard should tell users to send explicitly");
    const commandStatus = await page.locator("#office-command-status").innerText();
    assert.match(commandStatus, /下一步点“发送”开始/, "computer template should make the explicit start action clear");

    const sendButton = page.locator("button.command-footer__send").first();
    assert.equal(await sendButton.isEnabled(), true, "selected computer template should be submit-ready");
    await sendButton.click();
    await waitForCounter(() => (counters.runLaunchRequests ?? 0) >= 1, "computer template /api/runs launch request");

    const launch = counters.taskLaunchPayloads?.[0] ?? {};
    assert.equal(counters.taskLaunchRequests ?? 0, 1, "computer template send should create exactly one task/run request");
    assert.equal(counters.taskLaunchEndpoints?.[0], "/api/runs", "computer template should submit through /api/runs");
    assert.equal(launch.message, "帮我检查这台电脑", "computer template should submit the filled command text");
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
    const wizardText = await page.getByTestId("office-template-wizard").innerText();
    assert.match(wizardText, /任务向导/, "selected template should expose a compact task wizard");
    assert.match(wizardText, /删除前会停下审批/, "cleanup template should name the approval stop before destructive action");
    const commandStatus = await page.locator("#office-command-status").innerText();
    assert.match(commandStatus, /下一步点“发送”开始/, "selected prompt template should make the next executable action clear");
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

async function assertNaturalLanguageComputerCheckStartsReadOnlyRun(previewUrl) {
  const counters = {};
  const { context, page, profileDir } = await openDisposablePage();
  try {
    await installHealthyBackendMocks(page, counters, {
      runTasks: [
        {
          run_id: "run_natural_language_system_check_smoke",
          message: "帮我检查这台电脑",
          engine: "os",
          phase: "queued",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString()
        }
      ]
    });
    await gotoFirstLaunch(page, previewUrl);

    const commandInput = page.locator(".office-command-dock textarea");
    await commandInput.waitFor({ timeout: 15_000 });
    await commandInput.fill("帮我检查这台电脑");

    const sendButton = page.locator("button.command-footer__send").first();
    assert.equal(await sendButton.isEnabled(), true, "natural-language computer check should be submit-ready");
    await sendButton.click();

    await waitForCounter(() => (counters.runLaunchRequests ?? 0) >= 1, "natural-language /api/runs launch request");
    assert.equal(counters.chatLaunchRequests ?? 0, 0, "natural-language computer check should prefer /api/runs over legacy chat");
    assert.equal(counters.taskLaunchRequests ?? 0, 1, "natural-language computer check should create exactly one task/run request");

    const launch = counters.taskLaunchPayloads?.[0] ?? {};
    assert.equal(counters.taskLaunchEndpoints?.[0], "/api/runs", "natural-language computer check should submit through /api/runs");
    assert.equal(launch.message, "帮我检查这台电脑", "natural-language computer check should submit the user's exact first-task text");
    assert.equal(launch.engine, "auto", "renderer should let the backend route the read-only diagnostics engine");

    await page.getByText(/已开始处理任务/).first().waitFor({ timeout: 15_000 });

    const workspaceText = await page.getByTestId("task-workspace-card").innerText();
    assert.match(workspaceText, /系统只读/, "Task Workspace should classify the natural-language computer check as a read-only system task");

    const rootText = await page.locator("#root").innerText();
    assert.match(rootText, /帮我检查这台电脑/, "first screen should keep the submitted natural-language goal visible");
    assert.match(rootText, /电脑执行引擎|系统只读/, "first screen should expose OS/read-only execution feedback");
    assert.match(rootText, /等待执行|正在执行|排队中|处理中|进行中/, "first screen should show understandable task progress after natural-language submit");

    assert.ok(counters.systemDiagnosticsRequests >= 1, "natural-language first task should keep read-only diagnostics data available in the mocked path");
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
  await context.addInitScript(() => {
    const backendBaseUrl = "http://127.0.0.1:8000";
    const apiRequest = async (request) => {
      const url = new URL(request.endpoint, backendBaseUrl);
      if (request.query && typeof request.query === "object") {
        for (const [key, value] of Object.entries(request.query)) {
          if (value !== undefined && value !== null && value !== "") {
            url.searchParams.set(key, String(value));
          }
        }
      }
      const response = await fetch(url.toString(), {
        method: request.method ?? "GET",
        headers: request.body === undefined ? { Accept: "application/json" } : {
          Accept: "application/json",
          "Content-Type": "application/json"
        },
        body: request.body === undefined ? undefined : JSON.stringify(request.body)
      });
      let data = null;
      try {
        data = await response.json();
      } catch {
        data = null;
      }
      return response.ok
        ? { ok: true, status: response.status, data }
        : { ok: false, status: response.status, error: data?.detail ?? data?.error ?? response.statusText };
    };
    const emptyBrowserHostSnapshot = {
      sessions: [],
      events: [],
      activeSessionId: null,
      visible: false,
      hostAvailable: false
    };
    const unavailableBrowserHostAction = async () => ({
      ok: false,
      snapshot: emptyBrowserHostSnapshot,
      error: "Desktop browser host is unavailable in first-launch smoke"
    });
    window.lengrvis = {
      ...(window.lengrvis ?? {}),
      api: {
        request: apiRequest,
        abortInflight: async () => undefined
      },
      backend: {
        getStatus: async () => {
          const startedAt = Date.now();
          const health = await apiRequest({ endpoint: "/api/health", timeoutMs: 1500 });
          return {
            state: health.ok ? "running" : "stopped",
            baseUrl: backendBaseUrl,
            message: health.ok ? "后端已连接" : "等待后端连接",
            lastCheckedAt: new Date().toISOString(),
            health: { ok: health.ok, latencyMs: Date.now() - startedAt }
          };
        },
        start: async () => ({ ok: false, error: "not available in first-launch smoke" }),
        stop: async () => ({ ok: false, error: "not available in first-launch smoke" })
      },
      runs: {
        start: async (request) => apiRequest({ endpoint: "/api/runs", method: "POST", body: request })
      },
      browserHost: {
        getSnapshot: async () => emptyBrowserHostSnapshot,
        open: unavailableBrowserHostAction,
        show: unavailableBrowserHostAction,
        hide: unavailableBrowserHostAction,
        setBounds: unavailableBrowserHostAction,
        pause: unavailableBrowserHostAction,
        resume: unavailableBrowserHostAction,
        takeover: unavailableBrowserHostAction,
        release: unavailableBrowserHostAction,
        stop: unavailableBrowserHostAction,
        performAction: unavailableBrowserHostAction,
        onSnapshot: () => () => undefined
      },
      dialog: {
        chooseDirectory: async () => null,
        chooseDocument: async () => null,
        knownFolders: async () => ({
          desktop: null,
          downloads: null,
          documents: null,
          pictures: null
        }),
        chooseSkillDirectory: async () => null,
        chooseSkillZip: async () => null
      },
      shell: {
        openExternal: async () => undefined,
        getFileIcon: async () => null,
        showItemInFolder: async () => ({ ok: false, error: "not available in first-launch smoke" })
      },
      notifications: {
        show: async () => ({ shown: false, reason: "not available in first-launch smoke" }),
        onOpenTask: () => () => undefined
      },
      backendBaseUrl,
      consent: {
        getStatus: async () => ({
          consent: {
            privacy_version: "v1.0",
            eula_version: "v1.0",
            privacy_accepted_at: "2026-01-01T00:00:00.000Z",
            eula_accepted_at: "2026-01-01T00:00:00.000Z",
            installer_version: "0.1.0-smoke"
          },
          needsPrivacyConsent: false,
          needsEulaConsent: false
        }),
        accept: async (request) => ({
          privacy_version: "v1.0",
          eula_version: "v1.0",
          privacy_accepted_at: request?.acceptPrivacy === false ? "" : "2026-01-01T00:00:00.000Z",
          eula_accepted_at: request?.acceptEula === false ? "" : "2026-01-01T00:00:00.000Z",
          installer_version: "0.1.0-smoke"
        }),
        readDoc: async (docId) => ({ docId, content: "Smoke legal document." })
      },
      platform: "win32",
      versions: {
        app: "0.1.0-smoke",
        electron: "smoke",
        chrome: "smoke",
        node: "smoke"
      }
    };
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
  await revealHomeMoreDetails(page);
}

// The home inspector keeps the secondary status cards (trust, readiness,
// task pilot, workspace, outcomes) inside a collapsed "更多状态与详情"
// disclosure for progressive disclosure. Expand it so these assertions can
// read the detail cards that used to be visible by default.
async function revealHomeMoreDetails(page) {
  const details = page.getByTestId("home-more");
  await details.waitFor({ timeout: 15_000 });
  await details.evaluate((element) => {
    if (element instanceof HTMLDetailsElement) {
      element.open = true;
    }
  });
}

async function assertComputerTemplateHomeEvidence(page, { hasRecentResult }) {
  const templateButton = page.getByTestId("office-template-check-computer");
  await templateButton.waitFor({ timeout: 15_000 });
  await templateButton.hover();
  const expandedTemplateText = await templateButton.innerText();
  assert.match(expandedTemplateText, /产出/, "computer template should show its expected output when expanded");
  assert.match(expandedTemplateText, /健康状态|缺失依赖|下一步修复入口/, "computer template should name the visible result");
  assert.match(expandedTemplateText, /只读|不上云|无改动/, "computer template should expose the local read-only boundary");

  const outcomeCard = page.getByTestId("home-outcome-computer");
  await outcomeCard.waitFor({ timeout: 15_000 });
  const outcomeText = await outcomeCard.innerText();
  if (hasRecentResult) {
    assert.match(outcomeText, /系统检查完成结果已核验/, "computer template should render a verified outcome only when strict evidence exists");
    assert.match(outcomeText, /完成结果已核验/, "computer outcome should show the verified result label");
    assert.match(outcomeText, /下一步：查看电脑状态页/, "computer outcome should expose a next-step action");
    assert.match(outcomeText, /只读状态|不会改系统设置/, "computer outcome should preserve the local task boundary");

    const resultTimelineText = await page.getByTestId("home-result-timeline-card").innerText();
    assert.match(resultTimelineText, /结果时间线/, "home should expose the result-first timeline summary");
    assert.match(resultTimelineText, /完成结果已核验/, "result timeline should show the verified result state");
    assert.match(resultTimelineText, /结果可以作为完成记录|查看结果/, "result timeline should expose what the user can do next");
    assert.doesNotMatch(
      resultTimelineText,
      /tool_result|completion_evidence|result_verified|verified_result|completed result/i,
      "result timeline must not expose internal result contract names"
    );

    const workspaceText = await page.getByTestId("task-workspace-card").innerText();
    assert.match(workspaceText, /系统只读/, "Task Workspace should bind the computer template to a read-only system tool");
    assert.match(workspaceText, /已完成|按任务类型启用/, "Task Workspace should show local task evidence without faking a new run");
  } else {
    assert.match(outcomeText, /等待只读快照/, "computer template should have a clear fallback while no result exists");
    assert.match(outcomeText, /选择模板后点击发送/, "computer fallback should tell the user the next safe action");

    const resultTimelineText = await page.getByTestId("home-result-timeline-card").innerText();
    assert.match(resultTimelineText, /等待第一个任务|准备启动任务/, "home result timeline should explain the empty first-task state");
  }
}

async function assertTaskResultQualityStatesStayActionable(previewUrl) {
  await assertComputerResultQualityState(previewUrl, {
    task: unverifiedCompletedComputerTemplateTask,
    outcomeMatches: [
      /系统检查已有记录/,
      /有进度，待核验/,
      /还不能确认健康结论|查看时间线|重新检查/
    ],
    workspaceMatches: [
      /结果状态/,
      /有进度，待核验/,
      /不能当作最终结果/
    ],
    timelineMatches: [
      /结果时间线/,
      /有进度，待核验/,
      /缺少：结果核验|缺 结果核验/
    ],
    pilotMatches: [
      /已结束，待核验|核对结果|记录待核验/
    ],
    label: "visible-progress unverified task"
  });

  await assertComputerResultQualityState(previewUrl, {
    task: safeFailureComputerTemplateTask,
    outcomeMatches: [
      /任务未完成/,
      /安全停止，需处理/,
      /查看原因后重试/
    ],
    workspaceMatches: [
      /结果状态/,
      /安全停止，需处理/,
      /没有完成结果/
    ],
    timelineMatches: [
      /结果时间线/,
      /安全停止/,
      /查看原因后重试/
    ],
    pilotMatches: [
      /安全停止，需处理|查看原因|没有形成完成结果/
    ],
    label: "safe-failure task"
  });

  await assertComputerResultQualityState(previewUrl, {
    task: taskEvidenceOnlyComputerTemplateTask,
    outcomeMatches: [
      /系统检查已有记录/,
      /仅有任务记录/,
      /不能当作完成结果/
    ],
    workspaceMatches: [
      /结果状态/,
      /仅有任务记录/,
      /只说明任务被提交或创建/
    ],
    timelineMatches: [
      /结果时间线/,
      /仅有任务记录/,
      /缺少：完成结果、结果核验|缺 完成结果/
    ],
    pilotMatches: [
      /已结束，待核验|核对结果|记录待核验/
    ],
    label: "task-record-only task"
  });
}

async function assertComputerResultQualityState(previewUrl, { task, outcomeMatches, workspaceMatches, timelineMatches, pilotMatches, label }) {
  const counters = {};
  const { context, page, profileDir } = await openDisposablePage();
  try {
    await installHealthyBackendMocks(page, counters, { tasks: [task] });
    await gotoFirstLaunch(page, previewUrl);

    const outcomeCard = page.getByTestId("home-outcome-computer");
    await outcomeCard.waitFor({ timeout: 15_000 });
    const outcomeText = await waitForCardText(outcomeCard, outcomeMatches[0], `${label} outcome`, 15_000);
    for (const pattern of outcomeMatches) {
      assert.match(outcomeText, pattern, `${label} outcome should expose beginner-safe result quality`);
    }
    assert.doesNotMatch(outcomeText, /系统检查完成结果已核验|完成结果已核验/, `${label} outcome must not claim a verified completed result`);

    const workspaceText = await page.getByTestId("task-workspace-card").innerText();
    for (const pattern of workspaceMatches) {
      assert.match(workspaceText, pattern, `${label} workspace should explain the result-quality state`);
    }

    const resultTimelineText = await page.getByTestId("home-result-timeline-card").innerText();
    for (const pattern of timelineMatches) {
      assert.match(resultTimelineText, pattern, `${label} result timeline should summarize trust and next action`);
    }

    const pilotText = await page.locator(".task-pilot-card").first().innerText();
    for (const pattern of pilotMatches) {
      assert.match(pilotText, pattern, `${label} Task Pilot should stay honest and actionable`);
    }
    assert.doesNotMatch(pilotText, /完成结果已通过核验|查看结果/, `${label} Task Pilot should not show verified-result language without strict evidence`);

    const combinedText = `${outcomeText}\n${workspaceText}\n${resultTimelineText}\n${pilotText}`;
    assert.doesNotMatch(combinedText, /tool_result|completion_evidence|result_verified|safe_failure|task_evidence_only|completed result/i, `${label} UI must not expose internal result contract names`);
    assert.doesNotMatch(combinedText, /[A-Za-z]:\\|https?:\/\/\S*(?:token|api[_-]?key|authorization|access[_-]?token|sig|signature)=/i, `${label} UI must not expose local paths or tokenized URLs`);
    assert.doesNotMatch(combinedText, /证据/, `${label} UI should use novice-facing result and record language`);
  } finally {
    await context.close();
    removeTempDir(profileDir);
  }
}

async function assertHomeTrustBoundary(page) {
  const trustCard = page.locator(".home-trust-card").first();
  await trustCard.waitFor({ timeout: 15_000 });
  const trustText = await trustCard.innerText();
  assert.match(trustText, /隐私与权限/, "first screen should expose the trust boundary section");
  assert.match(trustText, /文件内容上传/, "trust boundary should name file-content upload policy");
  assert.match(trustText, /已关闭|需确认/, "trust boundary should show upload state in plain language");
  assert.match(trustText, /危险操作/, "trust boundary should name destructive-operation review");
  assert.match(trustText, /先审查/, "trust boundary should show review before dangerous operations");
  assert.match(trustText, /暂停等待确认/, "trust boundary should explain that destructive actions stop for approval");
}

async function expectText(locator, pattern, message, timeoutMs = 10_000) {
  await waitForCardText(locator, pattern, message, timeoutMs);
}

async function waitForCardText(locator, pattern, message, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  let lastText = "";
  while (Date.now() < deadline) {
    lastText = await locator.innerText().catch(() => "");
    if (pattern.test(lastText)) return lastText;
    await delay(100);
  }
  assert.match(lastText, pattern, message);
  return lastText;
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
  const runTasks = options.runTasks ?? [];
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
      count(counters, url.pathname === "/api/runs" ? "runLaunchRequests" : "chatLaunchRequests");
      recordTaskLaunchPayload(counters, request);
      counters.taskLaunchEndpoints = [...(counters.taskLaunchEndpoints ?? []), url.pathname];
    }
    if (url.pathname === "/api/system/info") {
      count(counters, "systemInfoRequests");
    }
    if (url.pathname === "/api/system/diagnostics") {
      count(counters, "systemDiagnosticsRequests");
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
        run_id: "run_natural_language_system_check_smoke",
        engine: "os",
        phase: "queued"
      });
    }
    if (url.pathname === "/api/chat/messages") return json([]);
    if (url.pathname === "/api/runs") return json(runTasks);
    if (url.pathname === "/api/tasks") return json(tasks);
    if (url.pathname.endsWith("/timeline")) return json({ messages: [], recordings: [] });
    if (url.pathname.endsWith("/agent-messages")) return json([]);
    if (url.pathname.endsWith("/safety-reviews")) return json([]);
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
  await stopProcessTree(child);
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
