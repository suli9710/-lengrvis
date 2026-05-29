const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const path = require("node:path");
const { chromium } = require("@playwright/test");

const previewUrl = "http://127.0.0.1:4173";
const desktopRoot = path.resolve(__dirname, "..");

const session = {
  id: "backend-only-session",
  task_id: "task-browser",
  current_url: "https://example.com/search?q=mavris",
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
      name: "Mavris.exe",
      cpu_percent: 2.4,
      memory_bytes: 268435456,
      status: "running"
    }
  ],
  suggestions: ["No critical system issue detected"]
};

const smokeDocumentPath = "C:\\Users\\Smoke\\Documents\\Quarterly Plan.md";
const smokeCompareDocumentPath = "C:\\Users\\Smoke\\Documents\\Quarterly Plan v2.md";

const forbiddenPlaceholderTexts = [
  "sample_contract.txt",
  "desktop/src/renderer/App.tsx",
  "desktop/src/main/ipc.ts",
  "approval-1",
  "C:\\Users\\Suli\\Desktop\\mavris",
  "审批队列里有一个待处理的高风险请求"
];

function assertNoSecretPayload(value, label) {
  const text = JSON.stringify(value);
  assert.equal(text.includes("secret-token"), false, `${label} should not include raw token values`);
  assert.equal(text.includes("password123"), false, `${label} should not include raw form text`);
  assert.equal(text.includes("top-secret observation"), false, `${label} should not include raw observed page text`);
  assert.equal(text.includes("#password"), false, `${label} should not include sensitive selectors`);
}

function startPreview() {
  console.log("starting Vite preview on 127.0.0.1:4173");
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

async function installApiMocks(page, options = {}) {
  const allowedDirectories = options.allowedDirectories ?? ["C:\\Users\\Smoke\\Documents"];
  const counters = options.counters;
  const fileSearchMode = options.fileSearchMode ?? "error";
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
      return json({ message: { id: "mock-response", role: "assistant", author: "Mavris", content: "mock", created_at: new Date().toISOString() } });
    }
    if (url.pathname === "/api/chat/messages") return json([]);
    if (url.pathname === "/api/tasks") return json([]);
    if (url.pathname === "/api/runs") return json([]);
    if (url.pathname === "/api/library") return json({
      section: url.searchParams.get("section") ?? "documents",
      roots: [],
      items: [],
      count: 0,
      total: 0,
      scanned: 0,
      truncated: false,
      stats: { size: 0, by_extension: {} }
    });
    if (url.pathname === "/api/current-plan") return json({});
    if (url.pathname === "/api/settings") return json({ allowed_directories: allowedDirectories });
    if (url.pathname === "/api/settings/llm/health") return json({});
    if (url.pathname === "/api/settings/llm/cost-summary") return json({});
    if (url.pathname === "/api/context/usage") return json({});
    if (url.pathname === "/api/audit/logs") return json([]);
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
    if (url.pathname === "/api/approvals/pending") return json([]);
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
          }
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
          }
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
  await page.waitForSelector("#root > *", { timeout: 15_000 });
  const rootText = await page.locator("#root").innerText();
  assert.ok(rootText.trim().length > 0, "root should not be blank");
  await assertButtonExists(page, /^(Refresh|刷新|鍒锋柊)$/);
}

async function assertNoPlaceholderContent(page, label) {
  const rootText = await page.locator("#root").innerText();
  for (const text of forbiddenPlaceholderTexts) {
    assert.equal(rootText.includes(text), false, `${label} should not show placeholder content: ${text}`);
  }
}

async function assertDocumentQuickEntry(page) {
  await page.getByRole("button", { name: /总结文档|鎬荤粨鏂囨。/ }).click();
  await page.getByText(/文档操作区|鏂囨。鎿嶄綔鍖?/).waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /选择文档|閫夋嫨鏂囨。/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /选择并总结|閫夋嫨骞舵€荤粨/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^读取$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^总结$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^提问$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^选择第二份$/ }).first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^对比$/ }).first().waitFor({ timeout: 10_000 });
  for (const label of [/^读取$/, /^总结$/, /^提问$/, /^对比$/]) {
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
  await page.getByRole("button", { name: /总结文档|鎬荤粨鏂囨。/ }).click();
  await page.getByText(/对比两份文档|瀵规瘮涓や唤鏂囨。/).waitFor({ timeout: 10_000 });
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
  await page.getByRole("button", { name: /查找大文件|鏌ユ壘澶ф枃浠?/ }).click();
  const commandInput = page.locator("textarea").first();
  await expectTextareaValue(commandInput, /找出这台电脑上最大的文件|鎵惧嚭/);
  await page.getByText(/已填好这句话，下一步点发送开始|宸插～濂借繖鍙ヨ瘽/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/理解目标|鐞嗚В鐩爣/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/实时显示进度|瀹炴椂鏄剧ず杩涘害/).first().waitFor({ timeout: 10_000 });
  assert.equal(counters.taskLaunchRequests ?? 0, 0, "quick prompt should fill the command box without starting a task");
  await assertNoHorizontalOverflow(page, "quick prompt entry");
}

async function assertComputerCheckEntry(page, counters) {
  const systemInfoRequestsBefore = counters.systemInfoRequests ?? 0;
  await page.getByRole("button", { name: /检查电脑|妫€鏌ョ數鑴?/ }).click();
  await page.getByText(/系统信息|绯荤粺淇℃伅/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/一键只读检查|涓€閿彧璇绘鏌?/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/只读诊断，不改设置|鍙璇婃柇锛屼笉鏀硅缃?/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/Mavris 连接|Mavris 杩炴帴/).first().waitFor({ timeout: 10_000 });
  await page.getByText(/任务状态|浠诲姟鐘舵€?/).first().waitFor({ timeout: 10_000 });
  assert.ok(
    (counters.systemInfoRequests ?? 0) > systemInfoRequestsBefore,
    "computer quick entry should refresh read-only system info"
  );
  assert.equal(counters.taskLaunchRequests ?? 0, 0, "computer quick entry should not start a chat or run task");
  await assertNoPlaceholderContent(page, "computer check quick entry");
  await assertNoHorizontalOverflow(page, "computer check quick entry");
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
  await page.getByRole("button", { name: /先选择范围|鍏堥€夋嫨鑼冨洿/ }).click();
  await page.getByText(/还没有选择范围|请先选择搜索范围|杩樻病鏈夐€夋嫨鑼冨洿/).first().waitFor({ timeout: 10_000 });
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
      await assertButtonExists(page, /^(Chat|对话|瀵硅瘽)$/);
      await assertNoHorizontalOverflow(page, `${viewport.label} home`);
      console.log(`viewport smoke passed: ${viewport.label} ${viewport.width}x${viewport.height}`);
      await page.close();
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
    Module._load = function patchedLoad(request, parent, isMain) {
      if (request === "electron") {
        return {
          BrowserWindow: {
            fromWebContents: (sender) => sender && sender.__trustedWindow ? sender.__trustedWindow : null
          },
          BrowserView: class BrowserView {},
          WebContentsView: class WebContentsView {},
          ipcMain: { handle: () => undefined },
          shell: { openExternal: async () => undefined }
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    try {
      const { BrowserHost } = require("../dist/main/browserHost.js");
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
      console.log("BrowserHost redaction smoke passed");
    } finally {
      Module._load = originalLoad;
    }
  } finally {
    if (browser) await browser.close();
    preview.kill("SIGTERM");
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
