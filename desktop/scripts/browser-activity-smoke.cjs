const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("@playwright/test");

const previewUrl = "http://127.0.0.1:4173";
const desktopRoot = path.resolve(__dirname, "..");
const browserActivityPanelSource = path.join(desktopRoot, "src", "renderer", "components", "BrowserActivityPanel.tsx");

const session = {
  id: "backend-only-session",
  task_id: "task-browser",
  current_url: "https://example.com/search?q=lengrvis",
  title: "Backend-only Browser Session",
  status: "running",
  mode: "agent",
  created_at: "2026-05-27T00:00:00.000Z",
  updated_at: "2026-05-27T00:01:00.000Z",
  paused: false,
  takeover: false,
  last_observation: null
};

const event = {
  id: "backend-event-1",
  session_id: session.id,
  task_id: session.task_id,
  type: "action.observe",
  action: { kind: "observe" },
  url: session.current_url,
  title: session.title,
  ok: true,
  created_at: "2026-05-27T00:01:30.000Z"
};

const systemDiagnostics = {
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
        used: 210 * 1024 * 1024 * 1024,
        free: 302 * 1024 * 1024 * 1024,
        percent: 41
      }
    }
  ],
  network: {},
  battery: null,
  top_processes: [
    {
      pid: 4120,
      name: "Lengrvis.exe",
      cpu_percent: 2.4,
      memory_bytes: 268435456,
      status: "running"
    }
  ],
  suggestions: ["No critical system issue detected"]
};

const smokeDocumentPath = "C:\\Users\\Smoke\\Documents\\Quarterly Plan.md";
const smokeCompareDocumentPath = "C:\\Users\\Smoke\\Documents\\Quarterly Plan v2.md";
const readyIndexStatus = {
  status: "ready",
  files_indexed: 3,
  chunks_indexed: 4,
  embeddings_indexed: 4,
  bytes_indexed: 8192,
  last_indexed_at: "2026-05-27T00:02:30.000Z",
  last_modified_at: "2026-05-27T00:02:00.000Z",
  retry_hint: "",
  latest_failure: null
};
const degradedIndexStatus = {
  ...readyIndexStatus,
  status: "degraded",
  latest_failure: {
    at: "2026-05-27T00:03:00.000Z",
    path_label: "Quarterly Plan.md",
    message: "embedding service offline"
  },
  retry_hint: "Retry rebuild after the local embedding service recovers."
};
const pilotTask = {
  id: "task-pilot-smoke",
  user_goal: "清理下载目录的大文件",
  status: "waiting_user_approval",
  mode: "efficiency",
  final_summary: "已生成清理预览，等待用户确认后再继续。",
  created_at: "2026-05-27T00:02:00.000Z",
  updated_at: "2026-05-27T00:03:00.000Z"
};
const pilotApproval = {
  id: "approval-pilot-smoke",
  approval_type: "cleanup",
  message: "准备清理下载目录的大文件，执行前需要你确认。",
  diff_preview: {
    summary: "将把 1 个大文件移入回收站。",
    items: [
      {
        path: "C:\\Users\\Smoke\\Downloads\\large-video.mp4",
        size_bytes: 734003200,
        disposition: "recycle_bin",
        reason: "体积较大且位于下载目录"
      }
    ],
    risk_warnings: ["执行前需要人工确认"]
  },
  status: "pending",
  created_at: "2026-05-27T00:03:30.000Z"
};

const forbiddenPlaceholderTexts = [
  "sample_contract.txt",
  "desktop/src/renderer/App.tsx",
  "desktop/src/main/ipc.ts",
  "approval-1",
  "C:\\Users\\Suli\\Desktop\\lengrvis",
  "审批队列里有一个待处理的高风险请求"
];

const quickTemplateButtonNames = {
  cleanDownloads: /整理下载目录|鏁寸悊涓嬭浇鐩綍/,
  summarizeDocument: /总结本地文档|总结文档|鎬荤粨鏈湴鏂囨。|鎬荤粨鏂囨。/,
  findLargeFiles: /查找大文件|鏌ユ壘澶ф枃浠?/,
  checkComputer: /检查电脑状态|检查电脑|妫€鏌ョ數鑴戠姸鎬?|妫€鏌ョ數鑴?/,
  documentQa: /文档问答|鏂囨。闂瓟/
};

function releasePreviewPort() {
  if (process.platform !== "win32") return;
  try {
    const output = execFileSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-Command",
        "$connections = Get-NetTCPConnection -LocalPort 4173 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($processId in $connections) { Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue }"
      ],
      { stdio: "pipe" }
    );
    if (String(output).trim()) process.stdout.write(output);
  } catch {
    // Port cleanup is best-effort; Vite strictPort will still fail loudly if another service remains.
  }
}

function assertNoSecretPayload(value, label) {
  const text = JSON.stringify(value);
  assert.equal(text.includes("secret-token"), false, `${label} should not include raw token values`);
  assert.equal(text.includes("password123"), false, `${label} should not include raw form text`);
  assert.equal(text.includes("top-secret observation"), false, `${label} should not include raw observed page text`);
  assert.equal(text.includes("#password"), false, `${label} should not include sensitive selectors`);
}

function assertBrowserHostFailureStopsBackendCommand() {
  const source = fs.readFileSync(browserActivityPanelSource, "utf8");
  assert.match(
    source,
    /if \(!result\.ok\) \{\s*onErrorChange\(result\.error \?\? `\$\{label\} failed`\);\s*return;\s*\}\s*if \(backendCommand\)/s,
    "BrowserActivityPanel must not call backend session commands after a failed BrowserHost command"
  );
}

function startPreview() {
  console.log("starting Vite preview on 127.0.0.1:4173");
  releasePreviewPort();
  const viteBin = path.join(desktopRoot, "node_modules", "vite", "bin", "vite.js");
  const child = spawn(process.execPath, [viteBin, "preview", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], {
    cwd: desktopRoot,
    stdio: ["ignore", "pipe", "pipe"]
  });
  child.stdout.on("data", (data) => process.stdout.write(data));
  child.stderr.on("data", (data) => process.stderr.write(data));
  return child;
}

async function waitForPreview() {
  console.log("waiting for Vite preview");
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(previewUrl);
      if (response.ok) return;
    } catch {
      // Keep polling until Vite preview is ready.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("Vite preview did not start in time");
}

async function installDesktopBridgeMocks(page) {
  await page.addInitScript(() => {
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
        : { ok: false, status: response.status, error: { message: data?.detail ?? data?.error ?? data?.message ?? response.statusText } };
    };
    const emptyBrowserHostSnapshot = {
      sessions: [],
      events: [],
      activeSessionId: null,
      visible: false,
      hostAvailable: false
    };
    const unavailableBrowserHostAction = async () => {
      window.__browserHostCalls = (window.__browserHostCalls ?? 0) + 1;
      return {
        ok: false,
        snapshot: emptyBrowserHostSnapshot,
        error: "Desktop browser host is unavailable in browser activity smoke"
      };
    };
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
        start: async () => ({ ok: false, error: "not available in browser activity smoke" }),
        stop: async () => ({ ok: false, error: "not available in browser activity smoke" }),
        foreground: async () => ({ ok: false, error: "not available in browser activity smoke" }),
        background: async () => ({ ok: false, error: "not available in browser activity smoke" })
      },
      runs: {
        start: async (request) => apiRequest({ endpoint: "/api/runs", method: "POST", body: request })
      },
      documents: {
        parse: async (request) => apiRequest({ endpoint: "/api/documents/parse", method: "POST", body: request }),
        ask: async (request) => apiRequest({ endpoint: "/api/documents/ask", method: "POST", body: request }),
        compare: async (request) => apiRequest({ endpoint: "/api/documents/compare", method: "POST", body: request })
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
      dialog: {
        chooseDirectory: async () => null,
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
        showItemInFolder: async (path) => apiRequest({ endpoint: "/api/apps/reveal", method: "POST", body: { path } })
          .then((response) => response.ok ? { ok: true, path, revealed: true } : { ok: false, error: response.error?.message ?? "无法打开所在位置" })
      },
      notifications: {
        show: async () => ({ shown: false, reason: "not available in browser activity smoke" }),
        onOpenTask: () => () => undefined
      },
      backendBaseUrl,
      platform: "win32",
      versions: {
        app: "0.1.0-smoke",
        electron: "smoke",
        chrome: "smoke",
        node: "smoke"
      }
    };
  });
}

async function installApiMocks(page, options = {}) {
  await installDesktopBridgeMocks(page);
  const allowedDirectories = options.allowedDirectories ?? ["C:\\Users\\Smoke\\Documents"];
  const counters = options.counters;
  const fileSearchMode = options.fileSearchMode ?? "error";
  const tasks = options.tasks ?? [];
  const approvals = options.approvals ?? [];
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:8000") {
      await route.continue();
      return;
    }

    const json = (body) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body)
    });

    if (url.pathname === "/api/health") return json({ status: "ok" });
    if (url.pathname === "/api/browser/sessions") return json({ ok: true, sessions: [session] });
    if (url.pathname === `/api/browser/session/${session.id}/events`) return json({ ok: true, events: [event] });
    if (url.pathname === "/api/browser/observe") return json({ ok: true, event });
    if (url.pathname === "/api/browser/replay-export") return json({ ok: true, events: [event], session });
    if ((url.pathname === "/api/chat" || url.pathname === "/api/runs") && request.method().toUpperCase() === "POST") {
      if (counters) counters.taskLaunchRequests = (counters.taskLaunchRequests ?? 0) + 1;
      return json({ message: { id: "mock-response", role: "assistant", author: "Lengrvis", content: "mock", created_at: new Date().toISOString() } });
    }
    if (url.pathname === "/api/chat/messages") return json([]);
    if (url.pathname === "/api/tasks") return json(tasks);
    if (url.pathname === "/api/runs") return json([]);
    if (url.pathname === "/api/library") return json({
      section: url.searchParams.get("section") ?? "documents",
      roots: [],
      items: [],
      count: 0,
      total: 0,
      scanned: 0,
      truncated: false,
      stats: { size: 0, by_extension: {} },
      index_status: readyIndexStatus
    });
    if (url.pathname === "/api/current-plan") return json({});
    if (url.pathname === "/api/settings") return json({ allowed_directories: allowedDirectories });
    if (url.pathname === "/api/settings/llm/health") return json({});
    if (url.pathname === "/api/settings/llm/cost-summary") return json({});
    if (url.pathname === "/api/context/usage") return json({});
    if (url.pathname === "/api/audit" || url.pathname === "/api/audit/logs") return json([]);
    if (url.pathname.endsWith("/timeline")) return json({ messages: [], recordings: [] });
    if (url.pathname === "/api/system/info") {
      if (counters) counters.systemInfoRequests = (counters.systemInfoRequests ?? 0) + 1;
      return json({ system: "Windows", platform: "win32", machine: "x64" });
    }
    if (url.pathname === "/api/system/diagnostics") return json(systemDiagnostics);
    if (url.pathname === "/api/system/processes") return json({ processes: systemDiagnostics.top_processes, count: systemDiagnostics.top_processes.length });
    if (url.pathname === "/api/system/startup-items") return json({ startup_items: [], count: 0 });
    if (url.pathname === "/api/apps") return json({ apps: [] });
    if (url.pathname === "/api/chat/proactive-suggestions") return json([]);
    if (url.pathname.endsWith("/agent-messages")) return json([]);
    if (url.pathname.endsWith("/safety-reviews")) return json([]);
    if (url.pathname === "/api/approvals/pending") return json(approvals);
    if (url.pathname === "/api/files/search") {
      if (counters) counters.fileSearchRequests = (counters.fileSearchRequests ?? 0) + 1;
      if (fileSearchMode === "empty") {
        return json({
          index_results: [],
          name_results: [],
          name_search: {
            count: 0,
            scanned: 42,
            truncated: false,
            status: "ok"
          },
          index_status: readyIndexStatus
        });
      }
      if (fileSearchMode === "success") {
        return json({
          index_results: [
            {
              file_id: "doc-quarterly-plan",
              path: smokeDocumentPath,
              snippet: "Quarterly Plan: launch checklist and customer notes"
            }
          ],
          name_results: [],
          name_search: {
            count: 1,
            scanned: 42,
            truncated: false,
            status: "ok"
          },
          index_status: degradedIndexStatus
        });
      }
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ message: "search backend unavailable" })
      });
    }
    if (url.pathname === "/api/documents/parse" && request.method().toUpperCase() === "POST") {
      if (counters) counters.documentParseRequests = (counters.documentParseRequests ?? 0) + 1;
      return json({
        id: "doc-quarterly-plan",
        path: smokeDocumentPath,
        title: "Quarterly Plan",
        text: "Launch checklist and customer notes.",
        blocks: [{ id: "block-1", type: "paragraph", text: "Launch checklist and customer notes." }],
        tables: [],
        citations: []
      });
    }
    if (url.pathname === "/api/documents/ask" && request.method().toUpperCase() === "POST") {
      if (counters) counters.documentAskRequests = (counters.documentAskRequests ?? 0) + 1;
      return json({
        answer: "这份文档主要是季度发布计划、检查清单和客户备注。",
        citations: [{ id: "cite-1", label: "Quarterly Plan", text: "Launch checklist and customer notes.", path: smokeDocumentPath }]
      });
    }
    if (url.pathname === "/api/documents/compare" && request.method().toUpperCase() === "POST") {
      if (counters) counters.documentCompareRequests = (counters.documentCompareRequests ?? 0) + 1;
      await new Promise((resolve) => setTimeout(resolve, 150));
      return json({
        summary: "第二份文档新增了上线日期和客户沟通负责人。",
        documents: [],
        differences: [
          {
            id: "diff-1",
            title: "上线日期",
            detail: "v2 增加了 6 月 12 日作为目标发布日期。",
            severity: "info"
          }
        ],
        tables: []
      });
    }
    if (url.pathname === "/api/apps/reveal" && request.method().toUpperCase() === "POST") {
      if (counters) counters.revealRequests = (counters.revealRequests ?? 0) + 1;
      return json({ ok: true, path: smokeDocumentPath, revealed: true });
    }

    return json({});
  });
}

async function assertRootRendered(page) {
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      await page.waitForSelector("#root > *", { timeout: 30_000 });
      const rootText = await page.locator("#root").innerText();
      assert.ok(rootText.trim().length > 0, "root should not be blank");
      await assertButtonExists(page, /^(Refresh|刷新|鍒锋柊)$/);
      await revealHomeMoreDetails(page);
      return;
    } catch (error) {
      if (attempt === 2) throw error;
      await page.reload({ waitUntil: "networkidle" });
    }
  }
}

// The home inspector keeps secondary status cards (trust, readiness, task
// pilot, workspace, outcomes) inside a collapsed "更多状态与详情" disclosure.
// Expand it so the assertions below can read those detail cards. Best-effort:
// non-home routes simply won't have the element.
async function revealHomeMoreDetails(page) {
  const details = page.getByTestId("home-more");
  try {
    await details.waitFor({ timeout: 10_000 });
  } catch {
    return;
  }
  await details.evaluate((element) => {
    if (element instanceof HTMLDetailsElement) {
      element.open = true;
    }
  });
}

async function assertNoPlaceholderContent(page, label) {
  const rootText = await page.locator("#root").innerText();
  for (const text of forbiddenPlaceholderTexts) {
    assert.equal(rootText.includes(text), false, `${label} should not show placeholder content: ${text}`);
  }
}

async function assertHomeQuickTemplates(page) {
  await page.getByTestId("office-template-check-computer").waitFor({ timeout: 20_000 });
  for (const name of Object.values(quickTemplateButtonNames)) {
    await page.getByRole("button", { name }).first().waitFor({ timeout: 20_000 });
  }
  await page.getByText(/任务工作区|Task Workspace/).first().waitFor({ timeout: 20_000 });
  await page.getByText(/成果区|鎴愭灉鍖?/).first().waitFor({ timeout: 20_000 });
  await assertComputerTemplateFallback(page);
}

async function assertComputerTemplateFallback(page) {
  const templateButton = page.getByTestId("office-template-check-computer");
  await templateButton.waitFor({ timeout: 10_000 });
  const templateText = await templateButton.innerText();
  assert.match(templateText, /产出/, "computer template should disclose the expected output before it is launched");
  assert.match(templateText, /健康状态|缺失依赖|下一步修复入口/, "computer template should name the result a user can verify");
  assert.match(templateText, /只读|不上云|无改动/, "computer template should keep the local read-only boundary visible");

  const outcomeText = await page.getByTestId("home-outcome-computer").innerText();
  assert.match(outcomeText, /等待只读快照/, "computer template fallback should not claim a result before one exists");
  assert.match(outcomeText, /可一键启动只读检查/, "computer template fallback should explain the next safe action");
}

async function assertDocumentQuickEntry(page) {
  await page.getByRole("button", { name: quickTemplateButtonNames.summarizeDocument }).click();
  await page.getByText(/文档操作区|鏂囨。鎿嶄綔鍖?/).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /选择文档|閫夋嫨鏂囨。/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /选择并总结|閫夋嫨骞舵€荤粨/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /读取预览|^读取$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /总结这份文档|^总结$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /向这份文档提问|^提问$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^选择第二份$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^对比$/ }).first().waitFor({ timeout: 10_000 });
  for (const label of [/读取预览|^读取$/, /总结这份文档|^总结$/, /向这份文档提问|^提问$/, /^对比$/]) {
    const actionButton = page.getByRole("button", { name: label }).first();
    assert.equal(await actionButton.isDisabled(), true, "document action should be disabled before a document is selected");
    assert.equal(await actionButton.getAttribute("data-loading"), null, "disabled document action should not be presented as loading");
  }
  await page.getByText(/同步当前范围|鍚屾褰撳墠鑼冨洿/).first().waitFor({ timeout: 10_000 });
  const rootText = await page.locator("#root").innerText();
  assert.ok(rootText.includes("选择文档") || rootText.includes("閫夋嫨鏂囨。"), "document quick entry should expose direct document picker");
  await page.getByRole("button", { name: /选择文档|閫夋嫨鏂囨。/ }).first().click();
  await page.getByText(/当前环境不能打开文档选择器|褰撳墠鐜涓嶈兘鎵撳紑鏂囨。/).first().waitFor({ timeout: 10_000 });
  await assertNoHorizontalOverflow(page, "document quick entry");
}

async function assertDocumentCompareFlow(page, counters) {
  await page.goto(previewUrl, { waitUntil: "networkidle" });
  await assertRootRendered(page);
  await page.getByRole("button", { name: quickTemplateButtonNames.summarizeDocument }).click();
  await page.getByText(/对比两份文档|瀵规瘮涓や唤鏂囨。/).first().waitFor({ timeout: 10_000 });
  await page.getByPlaceholder(/选择文档，或粘贴文件位置|閫夋嫨鏂囨。/).fill(smokeDocumentPath);
  await page.getByLabel(/第二份文档位置|绗簩浠芥枃妗ｄ綅缃?/).fill(smokeCompareDocumentPath);

  const compareButton = page.getByRole("button", { name: /^对比$/ }).first();
  assert.equal(await compareButton.isDisabled(), false, "compare should become available after both document paths are present");
  await compareButton.click();
  await page.getByRole("button", { name: /^对比中$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByText(/对比结果|瀵规瘮缁撴灉/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/上线日期|涓婄嚎鏃ユ湡/).first().waitFor({ timeout: 10_000 });
  assert.equal(counters.documentCompareRequests ?? 0, 1, "document compare should call compare API exactly once");
  await assertNoPlaceholderContent(page, "document compare flow");
  await assertNoHorizontalOverflow(page, "document compare flow");
}

async function assertQuickPromptEntry(page, counters) {
  await page.getByRole("button", { name: quickTemplateButtonNames.findLargeFiles }).click();
  const commandInput = page.locator("textarea").first();
  await expectTextareaValue(commandInput, /找出这台电脑上最大的文件|鎵惧嚭/);
  await page.getByText(/已填好这句话|宸插～濂借繖鍙ヨ瘽/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/理解目标|鐞嗚В鐩爣/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/清理前会确认|实时显示进度|瀹炴椂鏄剧ず杩涘害/).first().waitFor({ timeout: 10_000 });
  await assertRootTextIncludes(page, /(?:任务工作区|Task Workspace).*(?:文件工具|鏂囦欢宸ュ叿)|(?:文件工具|鏂囦欢宸ュ叿).*(?:任务工作区|Task Workspace)/s, "quick prompt should update Task Workspace");
  await assertRootTextIncludes(page, /发送后先选文件夹|清理前会确认|清理前不会删除任何文件/, "large-file quick entry should explain scope and deletion safety");
  assert.equal(counters.taskLaunchRequests ?? 0, 0, "quick prompt should fill the command box without starting a task");
  await assertNoHorizontalOverflow(page, "quick prompt entry");
}

async function assertComputerCheckEntry(page, counters) {
  const systemInfoRequestsBefore = counters.systemInfoRequests ?? 0;
  await page.getByRole("button", { name: quickTemplateButtonNames.checkComputer }).click();
  await page.getByText(/系统信息|绯荤粺淇℃伅/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/一键只读检查|立即只读检查|刷新本机状态|涓€閿彧璇绘鏌?/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/只读诊断，不改设置|鍙璇婃柇锛屼笉鏀硅缃?/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/Lengrvis 连接|Lengrvis 杩炴帴/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/任务状态|浠诲姟鐘舵€?/).first().waitFor({ timeout: 10_000 });
  await assertRootTextIncludes(page, /暂未读取.*不代表电脑异常|暂未读取不等于故障|未知.*不代表电脑异常|未知不等于故障/, "computer quick entry should explain unknown health state");
  assert.ok(
    (counters.systemInfoRequests ?? 0) > systemInfoRequestsBefore,
    "computer quick entry should refresh read-only system info"
  );
  assert.equal(counters.taskLaunchRequests ?? 0, 0, "computer quick entry should not start a chat or run task");
  await assertNoPlaceholderContent(page, "computer check quick entry");
  await assertNoHorizontalOverflow(page, "computer check quick entry");
}

async function assertTaskPilotCard(page) {
  await page.getByText(/任务驾驶舱|浠诲姟椹鹃┒鑸?/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/清理下载目录的大文件|娓呯悊涓嬭浇鐩綍/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/待审批|等待你的确认|需要你确认|寰呭鎵?/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/理解目标|鐞嗚В鐩爣/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/确认范围|纭鑼冨洿/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/结果留痕|缁撴灉鐣欑棔/).first().waitFor({ timeout: 10_000 });
  await assertNoPlaceholderContent(page, "task pilot card");
  await assertNoHorizontalOverflow(page, "task pilot card");
}

async function assertTaskPilotApprovalAction(page) {
  await page.getByRole("button", { name: /去确认|查看审批|鍘荤‘璁?/ }).first().click();
  await page.getByRole("dialog").waitFor({ timeout: 10_000 });
  await page.getByText(/清理计划审批|审批|准备清理下载目录的大文件/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/决策总览|鍐崇瓥鎬昏/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/安全核对/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/影响范围|褰卞搷鑼冨洿/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/恢复方式|鎭㈠鏂瑰紡/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/批准前不会移动或删除任何文件|等待你批准|鎵瑰噯鍓嶄笉浼氱Щ鍔ㄦ垨鍒犻櫎|绛夊緟浣犳壒鍑?/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/large-video\.mp4|下载目录的大文件|涓嬭浇鐩綍/).first().waitFor({ timeout: 10_000 });
  await assertNoHorizontalOverflow(page, "task pilot approval action");
}

async function assertRecentTaskRowAction(page) {
  const taskRow = page.locator(".task-row").filter({ hasText: /清理下载目录的大文件|娓呯悊涓嬭浇鐩綍/ }).first();
  if (await taskRow.count()) {
    await taskRow.click();
  } else {
    await page.getByRole("button", { name: /去确认|查看审批|鍘荤‘璁?/ }).first().click();
  }
  await page.getByRole("dialog").waitFor({ timeout: 10_000 });
  await page.getByText(/清理计划审批|审批|准备清理下载目录的大文件/).first().waitFor({ timeout: 10_000 });
  await assertNoHorizontalOverflow(page, "recent task row action");
}

async function assertFileSearchFailureState(page) {
  await page.goto(`${previewUrl}/?view=files`, { waitUntil: "networkidle" });
  await assertRootRendered(page);
  await page.getByPlaceholder(/搜索文件|鎼滅储鏂囦欢/).fill("anything");
  await page.getByRole("button", { name: /^(搜索|鎼滅储)$/ }).click();
  await page.getByRole("alert").filter({ hasText: "search backend unavailable" }).waitFor({ timeout: 10_000 });
  await assertNoPlaceholderContent(page, "file search failure");
}

async function assertFileSearchEmptyState(page, counters) {
  await page.goto(`${previewUrl}/?view=files`, { waitUntil: "networkidle" });
  await assertRootRendered(page);
  await page.getByPlaceholder(/搜索文件|鎼滅储鏂囦欢/).fill("definitely-no-match");
  await page.getByRole("button", { name: /^(搜索|鎼滅储)$/ }).click();
  await page.getByText(/没有找到结果|没有找到完整结果|娌℃湁鎵惧埌缁撴灉/).first().waitFor({ timeout: 10_000 });
  const rootText = await page.locator("#root").innerText();
  assert.equal(rootText.includes("这次搜索未完成"), false, "empty search should not be presented as a failed search");
  assert.equal(counters.fileSearchRequests ?? 0, 1, "empty search should call files search API exactly once");
  await assertNoPlaceholderContent(page, "file search empty");
  await assertNoHorizontalOverflow(page, "file search empty");
}

async function searchSuccessfulDocument(page) {
  await page.goto(`${previewUrl}/?view=files`, { waitUntil: "networkidle" });
  await assertRootRendered(page);
  await page.getByPlaceholder(/搜索文件|鎼滅储鏂囦欢/).fill("quarterly");
  await page.getByRole("button", { name: /^(搜索|鎼滅储)$/ }).click();
  await page.getByText("Quarterly Plan.md").first().waitFor({ timeout: 10_000 });
  await page.getByText(/Users\/Smoke\/Documents|Smoke\/Documents/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/launch checklist and customer notes/i).first().waitFor({ timeout: 10_000 });
  await page.getByText(/索引可用但需要留意|embedding service offline/).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^读取$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^总结$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^打开位置$/ }).first().waitFor({ timeout: 10_000 });
  await assertNoPlaceholderContent(page, "file search success result");
  await assertNoHorizontalOverflow(page, "file search success result");
}

async function assertFileSearchResultActions(browser) {
  for (const action of ["read", "summarize", "reveal"]) {
    const counters = { fileSearchRequests: 0, documentParseRequests: 0, documentAskRequests: 0, revealRequests: 0 };
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installApiMocks(page, { counters, fileSearchMode: "success" });
    await searchSuccessfulDocument(page);

    if (action === "read") {
      await page.getByRole("button", { name: /^读取$/ }).first().click();
      await page.getByText(/已读取，可查看下方预览|宸茶鍙栵紝鍙煡鐪嬩笅鏂归瑙?/).first().waitFor({ timeout: 10_000 });
      await returnToSearchTab(page);
      await assertRootTextIncludes(page, /已切到文档操作区并读取完成|宸插垏鍒版枃妗ｆ搷浣滃尯/, "read action should leave a result-card success message");
      assert.equal(counters.documentParseRequests, 1, "read action should parse the selected result once");
      assert.equal(counters.documentAskRequests, 0, "read action should not ask/summarize the document");
    } else if (action === "summarize") {
      await page.getByRole("button", { name: /^总结$/ }).first().click();
      await page.getByText(/已生成总结，可以继续追问|宸茬敓鎴愭€荤粨/).first().waitFor({ timeout: 10_000 });
      await returnToSearchTab(page);
      await assertRootTextIncludes(page, /已切到文档操作区并生成总结|宸插垏鍒版枃妗ｆ搷浣滃尯/, "summarize action should leave a result-card success message");
      assert.equal(counters.documentAskRequests, 1, "summarize action should ask the selected result once");
      assert.equal(counters.documentParseRequests, 0, "summarize action should not parse via the read endpoint");
    } else {
      await page.getByRole("button", { name: /^打开位置$/ }).first().click();
      await page.getByText(/已打开文件所在位置|宸叉墦寮€鏂囦欢鎵€鍦ㄤ綅缃?/).first().waitFor({ timeout: 10_000 });
      assert.equal(counters.revealRequests, 1, "open location action should reveal the selected result once");
      assert.equal(counters.documentParseRequests, 0, "open location should not parse the document");
      assert.equal(counters.documentAskRequests, 0, "open location should not summarize the document");
    }

    assert.equal(counters.fileSearchRequests, 1, `${action} flow should perform one file search`);
    await assertNoHorizontalOverflow(page, `file search result ${action} action`);
    await page.close();
  }
}

async function assertMissingScopeSearchIsLocal(page, counters) {
  await page.goto(`${previewUrl}/?view=files`, { waitUntil: "networkidle" });
  await assertRootRendered(page);
  await page.getByPlaceholder(/搜索文件|鎼滅储鏂囦欢/).fill("anything");
  await page.getByRole("button", { name: /先选要找的文件夹|先选择范围|鍏堥€夋嫨鑼冨洿/ }).click();
  await page.getByText(/还没有选择要查找的文件夹|请先选择要查找的文件夹|还没有选择范围|请先选择搜索范围|杩樻病鏈夐€夋嫨鑼冨洿/).first().waitFor({ timeout: 10_000 });
  await assertRootTextIncludes(page, /只会扫描你选择的文件夹|清理前不会删除任何文件/, "missing scope guide should reassure users about scope and deletion");
  assert.equal(counters.fileSearchRequests ?? 0, 0, "missing scope search should not call files search API");
  await assertNoPlaceholderContent(page, "file search missing scope");
  await assertNoHorizontalOverflow(page, "file search missing scope");
}

async function assertMissingQuerySearchIsLocal(page, counters) {
  await page.goto(`${previewUrl}/?view=files`, { waitUntil: "networkidle" });
  await assertRootRendered(page);
  await page.getByRole("button", { name: /先输入关键词|鍏堣緭鍏ュ叧閿瘝/ }).click();
  await page.getByText(/还没有输入关键词|请输入要查找的文件名或关键词|杩樻病鏈夎緭鍏ュ叧閿瘝/).first().waitFor({ timeout: 10_000 });
  assert.equal(counters.fileSearchRequests ?? 0, 0, "missing query search should not call files search API");
  await assertNoPlaceholderContent(page, "file search missing query");
  await assertNoHorizontalOverflow(page, "file search missing query");
}

async function assertButtonExists(page, name) {
  await page.getByRole("button", { name }).first().waitFor({ timeout: 10_000 });
}

async function expectTextareaValue(locator, pattern) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const value = await locator.inputValue().catch(() => "");
    if (pattern.test(value)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const value = await locator.inputValue().catch(() => "");
  assert.match(value, pattern, "textarea should contain the quick prompt");
}

async function isDisabledButton(page, name) {
  return page.getByRole("button", { name }).first().isDisabled();
}

async function assertNoHorizontalOverflow(page, label) {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return {
      scrollWidth: root.scrollWidth,
      clientWidth: root.clientWidth,
      overflowBy: root.scrollWidth - root.clientWidth
    };
  });
  assert.ok(overflow.overflowBy <= 1, `${label} should not overflow horizontally: ${JSON.stringify(overflow)}`);
}

async function assertRootTextIncludes(page, pattern, label) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const rootText = await page.locator("#root").innerText().catch(() => "");
    if (pattern.test(rootText)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const rootText = await page.locator("#root").innerText().catch(() => "");
  assert.match(rootText, pattern, label);
}

async function returnToSearchTab(page) {
  await page.getByRole("tab", { name: /搜索|鎼滅储/ }).first().click();
  await page.getByRole("button", { name: /^读取$/ }).first().waitFor({ timeout: 10_000 });
}

(async () => {
  const preview = startPreview();
  let browser;
  try {
    await waitForPreview();
    assertBrowserHostFailureStopsBackendCommand();
    console.log("launching Chromium");
    browser = await chromium.launch();

    for (const viewport of [
      { width: 1366, height: 768, label: "desktop" },
      { width: 390, height: 844, label: "mobile" }
    ]) {
      const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      await installApiMocks(page);
      await page.goto(previewUrl, { waitUntil: "networkidle" });
      await assertRootRendered(page);
      await assertNoPlaceholderContent(page, `${viewport.label} empty backend`);
      await assertHomeQuickTemplates(page);
      await assertButtonExists(page, /^(Chat|对话|瀵硅瘽)$/);
      await assertNoHorizontalOverflow(page, `${viewport.label} home`);
      console.log(`viewport smoke passed: ${viewport.label} ${viewport.width}x${viewport.height}`);
      await page.close();
    }

    console.log("checking task pilot lifecycle card");
    for (const viewport of [
      { width: 1366, height: 768, label: "desktop" },
      { width: 390, height: 844, label: "mobile" }
    ]) {
      const pilotPage = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      await installApiMocks(pilotPage, { tasks: [pilotTask], approvals: [pilotApproval] });
      await pilotPage.goto(previewUrl, { waitUntil: "networkidle" });
      await assertRootRendered(pilotPage);
      await assertTaskPilotCard(pilotPage);
      await assertTaskPilotApprovalAction(pilotPage);
      await pilotPage.close();

      const rowPage = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      await installApiMocks(rowPage, { tasks: [pilotTask], approvals: [pilotApproval] });
      await rowPage.goto(previewUrl, { waitUntil: "networkidle" });
      await assertRootRendered(rowPage);
      await assertRecentTaskRowAction(rowPage);
      await rowPage.close();
    }

    console.log("checking placeholder-free entry points");
    for (const viewport of [
      { width: 1366, height: 768, label: "desktop" },
      { width: 390, height: 844, label: "mobile" }
    ]) {
      const homePage = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      const quickPromptCounters = { taskLaunchRequests: 0 };
      await installApiMocks(homePage, { counters: quickPromptCounters });
      await homePage.goto(previewUrl, { waitUntil: "networkidle" });
      await assertRootRendered(homePage);
      await assertNoPlaceholderContent(homePage, `${viewport.label} home`);
      await assertQuickPromptEntry(homePage, quickPromptCounters);
      await homePage.close();

      const computerPage = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      const computerCounters = { systemInfoRequests: 0, taskLaunchRequests: 0 };
      await installApiMocks(computerPage, { counters: computerCounters });
      await computerPage.goto(previewUrl, { waitUntil: "networkidle" });
      await assertRootRendered(computerPage);
      await assertNoPlaceholderContent(computerPage, `${viewport.label} home before computer check`);
      await assertComputerCheckEntry(computerPage, computerCounters);
      await computerPage.close();

      const documentPage = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      await installApiMocks(documentPage);
      await documentPage.goto(previewUrl, { waitUntil: "networkidle" });
      await assertRootRendered(documentPage);
      await assertNoPlaceholderContent(documentPage, `${viewport.label} home before document entry`);
      await assertDocumentQuickEntry(documentPage);
      await assertNoPlaceholderContent(documentPage, `${viewport.label} document quick entry`);
      await documentPage.close();

      const documentComparePage = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      const documentCompareCounters = { documentCompareRequests: 0 };
      await installApiMocks(documentComparePage, { counters: documentCompareCounters });
      await assertDocumentCompareFlow(documentComparePage, documentCompareCounters);
      await documentComparePage.close();
    }

    console.log("checking file search failure state");
    console.log("checking file search missing-scope guard");
    const missingScopeCounters = { fileSearchRequests: 0 };
    const missingScopePage = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installApiMocks(missingScopePage, { allowedDirectories: [], counters: missingScopeCounters });
    await assertMissingScopeSearchIsLocal(missingScopePage, missingScopeCounters);
    await missingScopePage.close();

    console.log("checking file search missing-query guard");
    const missingQueryCounters = { fileSearchRequests: 0 };
    const missingQueryPage = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installApiMocks(missingQueryPage, { counters: missingQueryCounters });
    await assertMissingQuerySearchIsLocal(missingQueryPage, missingQueryCounters);
    await missingQueryPage.close();

    const filesPage = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    await installApiMocks(filesPage);
    await assertFileSearchFailureState(filesPage);
    await filesPage.close();

    console.log("checking file search empty state");
    const emptySearchCounters = { fileSearchRequests: 0 };
    const emptySearchPage = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installApiMocks(emptySearchPage, { counters: emptySearchCounters, fileSearchMode: "empty" });
    await assertFileSearchEmptyState(emptySearchPage, emptySearchCounters);
    await emptySearchPage.close();

    console.log("checking file search result actions on mobile");
    await assertFileSearchResultActions(browser);

    console.log("checking Browser Activity backend-only session");
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    await installApiMocks(page);
    await page.goto(`${previewUrl}/?view=browser`, { waitUntil: "networkidle" });
    await assertRootRendered(page);

    await page.getByText(session.title).first().waitFor({ timeout: 10_000 });
    await page.getByText("action.observe").waitFor({ timeout: 10_000 });

    for (const [label, matcher] of [
      ["Pause", /^(Pause|暂停|鏆傚仠)$/],
      ["Take Over", /^(Take Over|Take over|接管|鎺ョ)$/],
      ["Stop", /^(Stop|停止|鍋滄)$/]
    ]) {
      await assert.equal(await isDisabledButton(page, matcher), true, `${label} should be disabled without an Electron host session`);
    }
    await assert.equal(await isDisabledButton(page, /^(Hide|隐藏|闅愯棌)$/), true, "Hide should be disabled when Electron host is absent");

    const requestCountBefore = await page.evaluate(() => window.__browserHostCalls ?? 0);
    await page.getByRole("button", { name: /^(Pause|暂停|鏆傚仠)$/ }).click({ force: true });
    const requestCountAfter = await page.evaluate(() => window.__browserHostCalls ?? 0);
    assert.equal(requestCountAfter, requestCountBefore, "disabled host-only controls should not call Electron host actions");

    console.log("Browser Activity backend-only smoke passed");
    await page.close();

    console.log("checking BrowserHost output redaction");
    const Module = require("node:module");
    const originalLoad = Module._load;
    let openedExternalUrls = 0;
    const dnsLookups = [];
    Module._load = function patchedLoad(request, parent, isMain) {
      if (request === "electron") {
        return {
          BrowserWindow: {
            fromWebContents: (sender) => sender && sender.__trustedWindow ? sender.__trustedWindow : null
          },
          BrowserView: class BrowserView {},
          WebContentsView: class WebContentsView {},
          ipcMain: { handle: () => undefined },
          shell: { openExternal: async () => { openedExternalUrls += 1; } }
        };
      }
      if (request === "node:dns/promises") {
        return {
          lookup: async (hostname) => {
            dnsLookups.push(hostname);
            if (hostname === "private.example.test") {
              return [{ address: "127.0.0.1", family: 4 }];
            }
            if (hostname === "unresolved.example.test") {
              throw new Error("dns unavailable");
            }
            return [{ address: "93.184.216.34", family: 4 }];
          }
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    try {
      const {
        BrowserHost,
        BrowserHostWebSocketBridge,
        buildBrowserHostWebSocketUrl,
        hardenEmbeddedWebContents,
        isLoopbackBackendUrl
      } = require("../dist/main/browserHost.js");
      const host = new BrowserHost(() => null);
      host.sessions = new Map([
        [
          "sensitive",
          {
            container: {},
            session: {
            id: "sensitive",
            current_url: "https://example.test/callback?token=secret-token&safe=1",
            title: "Sensitive",
            status: "idle",
            mode: "watch",
            created_at: "2026-05-27T00:00:00.000Z",
            updated_at: "2026-05-27T00:00:00.000Z",
            paused: false,
            takeover: false,
            last_observation: {
              url: "https://example.test/page?code=secret-token&client_secret=secret-token&session_id=secret-token",
              text: "top-secret observation token=secret-token password=password123",
              links: [{ text: "top-secret observation", url: "https://example.test/a?password=secret-token&oauth_token=secret-token" }]
            }
            },
            events: [{
              id: "event-sensitive",
              session_id: "sensitive",
              type: "action.fill",
              action: { kind: "fill", selector: "#password", text: "password123", fields: { "#password": "password123" } },
              url: "https://example.test/form?token=secret-token&auth_token=secret-token",
              ok: true,
              screenshot_url: "data:image/png;base64,secret-token",
              created_at: "2026-05-27T00:00:01.000Z"
            }]
          }
        ]
      ]);
      const redactedSnapshot = host.getSnapshot();
      assertNoSecretPayload(redactedSnapshot, "BrowserHost snapshot");

      let windowOpenHandler;
      let willNavigateHandler;
      let beforeRequestHandler;
      const hardenedWebContents = {
        setWindowOpenHandler: (handler) => {
          windowOpenHandler = handler;
        },
        on: (name, handler) => {
          if (name === "will-navigate") {
            willNavigateHandler = handler;
          }
        },
        session: {
          webRequest: {
            onBeforeRequest: (handler) => {
              beforeRequestHandler = handler;
            }
          },
          setPermissionRequestHandler: () => undefined,
          setPermissionCheckHandler: () => undefined
        },
        setAudioMuted: () => undefined
      };
      hardenEmbeddedWebContents(hardenedWebContents);
      assert.ok(windowOpenHandler, "embedded BrowserHost webContents should install a window.open handler");
      assert.ok(willNavigateHandler, "embedded BrowserHost webContents should install a will-navigate guard");
      assert.ok(beforeRequestHandler, "embedded BrowserHost webContents should install a request guard");
      assert.deepEqual(
        windowOpenHandler({ url: "https://example.test" }),
        { action: "deny" },
        "embedded BrowserHost window.open should be denied by default"
      );
      assert.equal(openedExternalUrls, 0, "embedded BrowserHost window.open must not shell.openExternal automatically");
      let privatePrevented = false;
      willNavigateHandler({ preventDefault: () => { privatePrevented = true; } }, "http://127.0.0.1:8000/admin");
      assert.equal(privatePrevented, true, "BrowserHost must block loopback navigation by default");
      let compressedIpv6Prevented = false;
      willNavigateHandler({ preventDefault: () => { compressedIpv6Prevented = true; } }, "http://[0::1]:8000/admin");
      assert.equal(compressedIpv6Prevented, true, "BrowserHost must block compressed IPv6 loopback navigation by default");
      process.env.LENGRVIS_BROWSER_HOST_ALLOW_PRIVATE_NETWORK = "1";
      let devPrivatePrevented = false;
      willNavigateHandler({ preventDefault: () => { devPrivatePrevented = true; } }, "http://127.0.0.1:8000/admin");
      delete process.env.LENGRVIS_BROWSER_HOST_ALLOW_PRIVATE_NETWORK;
      assert.equal(devPrivatePrevented, false, "BrowserHost private network navigation requires an explicit development opt-in");
      let publicPrevented = false;
      willNavigateHandler({ preventDefault: () => { publicPrevented = true; } }, "https://example.test/");
      assert.equal(publicPrevented, false, "BrowserHost must allow public http(s) navigation");
      const browserHostRequestCanceled = (url) => new Promise((resolve) => {
        beforeRequestHandler({ url }, (response) => resolve(Boolean(response.cancel)));
      });
      assert.equal(
        await browserHostRequestCanceled("https://private.example.test/dashboard"),
        true,
        "BrowserHost must block hostnames that resolve to loopback/private addresses"
      );
      assert.equal(
        await browserHostRequestCanceled("ws://127.0.0.1:8000/socket"),
        true,
        "BrowserHost must block non-HTTP requests to private-network hosts"
      );
      assert.equal(
        await browserHostRequestCanceled("https://public.example.test/"),
        false,
        "BrowserHost must allow hostnames that resolve to public addresses"
      );
      assert.equal(
        await browserHostRequestCanceled("https://unresolved.example.test/"),
        true,
        "BrowserHost must fail closed when DNS safety checks cannot verify a hostname"
      );
      assert.deepEqual(dnsLookups, ["private.example.test", "public.example.test", "unresolved.example.test"]);

      assert.equal(isLoopbackBackendUrl("http://127.0.0.1:8000"), true);
      assert.equal(isLoopbackBackendUrl("http://localhost:8000"), true);
      assert.equal(isLoopbackBackendUrl("http://[::1]:8000"), true);
      assert.equal(isLoopbackBackendUrl("http://192.168.1.10:8000"), false);
      const wsUrl = new URL(buildBrowserHostWebSocketUrl("http://127.0.0.1:8000"));
      assert.equal(wsUrl.protocol, "ws:");
      assert.equal(wsUrl.pathname, "/api/ws/browser-host");
      assert.equal(wsUrl.searchParams.get("desktop_token"), null);
      assert.throws(
        () => buildBrowserHostWebSocketUrl("https://control.example.test"),
        /loopback/
      );

      const originalWebSocket = global.WebSocket;
      const sentMessages = [];
      class FakeWebSocket {
        static OPEN = 1;
        readyState = FakeWebSocket.OPEN;
        listeners = {};

        constructor(url, protocols) {
          this.url = url;
          this.protocols = protocols;
          FakeWebSocket.last = this;
        }

        addEventListener(name, handler) {
          this.listeners[name] = handler;
        }

        send(payload) {
          sentMessages.push(JSON.parse(payload));
        }

        close() {
          this.readyState = 3;
        }
      }
      global.WebSocket = FakeWebSocket;
      try {
        const bridge = new BrowserHostWebSocketBridge(host, () => "http://127.0.0.1:8000", () => "desktop-secret");
        bridge.start();
        assert.equal(new URL(FakeWebSocket.last.url).searchParams.get("desktop_token"), null);
        assert.deepEqual(FakeWebSocket.last.protocols, ["lengrvis.desktop.token.desktop-secret"]);
        FakeWebSocket.last.listeners.open();
        assert.equal(sentMessages[0].type, "snapshot", "BrowserHost WS bridge should send snapshots after protocol auth");
        FakeWebSocket.last.listeners.message({
          data: JSON.stringify({ type: "takeover", request_id: "takeover-1", session_id: "sensitive" })
        });
        FakeWebSocket.last.listeners.message({
          data: JSON.stringify({
            type: "action",
            request_id: "action-1",
            session_id: "sensitive",
            action: { kind: "click", selector: "#submit" }
          })
        });
        await new Promise((resolve) => setTimeout(resolve, 0));
        const remoteWriteResults = sentMessages.filter((message) => message.type === "result");
        assert.equal(remoteWriteResults.length, 2, "BrowserHost WS bridge should answer denied remote write requests");
        assert.deepEqual(
          remoteWriteResults.map((message) => [message.request_id, message.ok]),
          [
            ["takeover-1", false],
            ["action-1", false]
          ],
          "BrowserHost WS bridge must not execute remote takeover or action messages without a grant"
        );
        assert.match(remoteWriteResults[0].error, /approval grant/);
        assert.match(remoteWriteResults[1].error, /approval grant/);
        bridge.stop();
      } finally {
        global.WebSocket = originalWebSocket;
      }
      console.log("BrowserHost redaction smoke passed");
    } finally {
      Module._load = originalLoad;
    }
  } finally {
    if (browser) await browser.close();
    preview.kill("SIGTERM");
    releasePreviewPort();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
