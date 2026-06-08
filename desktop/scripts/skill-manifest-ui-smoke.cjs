const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { chromium } = require("@playwright/test");

const root = path.resolve(__dirname, "..", "..");
const desktopRoot = path.resolve(__dirname, "..");
const previewHost = "127.0.0.1";
const backendOrigin = "http://127.0.0.1:8000";
const previewReadyTimeoutMs = 30_000;
const screenshotPath = path.join(root, ".tmp", "qa-evidence", "skill-manifest-ui-smoke.png");

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

function assertIncludes(text, needle, label) {
  if (!text.includes(needle)) {
    throw new Error(`${label} missing: ${needle}`);
  }
}

function assertMatches(text, pattern, label) {
  if (!pattern.test(text)) {
    throw new Error(`${label} missing pattern: ${pattern}`);
  }
}

function assertDoesNotMatch(text, pattern, label) {
  if (pattern.test(text)) {
    throw new Error(`${label} unexpectedly matched pattern: ${pattern}`);
  }
}

async function main() {
  assertSourceBoundaries();
  assertBuiltRendererExists();
  await assertRenderedManifestBoundary();
  console.log(`skill-manifest-ui-smoke passed; screenshot: ${screenshotPath}`);
}

function assertSourceBoundaries() {
  const sample = read("test_data/skills/product_manifest_showcase/skill.yaml");
  for (const permission of [
    "filesystem.read",
    "filesystem.write",
    "filesystem.delete",
    "ui.control",
    "network.external",
    "messaging.send"
  ]) {
    assertIncludes(sample, permission, "showcase manifest permission");
  }
  assertIncludes(sample, "supports_dry_run: true", "showcase manifest preview");
  assertIncludes(sample, "smoke_tests:", "showcase manifest smoke metadata");
  assertMatches(sample, /rollback_hint:.*(handoff|hand off)/is, "showcase manifest rollback or handoff copy");

  const service = read("backend/app/services/skill_service.py");
  assertIncludes(service, '"permissions": definition.effective_permissions(tool)', "backend skill catalog permissions");
  assertIncludes(service, '"supports_dry_run": tool.supports_dry_run', "backend skill catalog preview flag");
  assertIncludes(service, '"rollback_hint": tool.rollback_hint', "backend skill catalog rollback hint");

  const sharedTypes = read("desktop/src/shared/types.ts");
  assertIncludes(sharedTypes, "permissions: string[];", "desktop skill type permissions");
  assertIncludes(sharedTypes, "supportsDryRun: boolean;", "desktop skill type preview flag");
  assertIncludes(sharedTypes, "rollbackHint: string;", "desktop skill type rollback hint");

  const apiClient = read("desktop/src/renderer/lib/apiClient.ts");
  assertIncludes(apiClient, "permissions: Array.isArray(tool.permissions) ? tool.permissions.map(String) : []", "desktop API permission mapping");
  assertIncludes(apiClient, "supportsDryRun: Boolean(tool.supports_dry_run)", "desktop API preview mapping");
  assertIncludes(apiClient, "rollbackHint: String(tool.rollback_hint ?? \"\")", "desktop API rollback mapping");

  const skillsView = read("desktop/src/renderer/views/SkillsView.tsx");
  for (const label of ["读文件", "写文件", "操作 UI", "访问网络", "发送消息", "删除数据", "Preview", "Rollback/Handoff"]) {
    assertIncludes(skillsView, `label: "${label}"`, "Product Manifest card label");
  }
  assertIncludes(skillsView, "Manifest 声明权限与文本推断信号", "Product Manifest mixed-source aria label");
  assertIncludes(skillsView, "权限以 Manifest 声明为准，文本提示只作提醒", "Product Manifest source boundary copy");
  assertIncludes(skillsView, "source: \"Manifest 声明\"", "Product Manifest declared source copy");
  assertIncludes(skillsView, "source: \"文本提示\"", "Product Manifest inferred source copy");
  assertIncludes(skillsView, "\"安全检查\"", "Product Manifest guardrail source copy");
  assertIncludes(skillsView, "source: \"未声明\"", "Product Manifest absent source copy");
  assertIncludes(skillsView, "function skillSignalText", "Product Manifest signal text helper");
  assertIncludes(skillsView, "function skillPermissions", "Product Manifest declared permission helper");
  assertDoesNotMatch(
    skillsView,
    /function skillSignalText[\s\S]*\.\.\.tool\.permissions[\s\S]*function skillPermissions/,
    "Product Manifest signal text must not consume declared permissions"
  );
  for (const pattern of [
    /const declaredReadsFiles = hasPermission\(permissions, \/\^filesystem\\\.read/,
    /const signalReadsFiles = matches\(signalText,/,
    /const declaredWritesFiles = hasPermission\(permissions, \/\^filesystem\\\.write/,
    /const signalWritesFiles = matches\(signalText,/,
    /const declaredDelete = hasPermission\(permissions, \/\^filesystem\\\.delete/,
    /const signalDelete = matches\(signalText,/,
    /const declaredControlsUi = hasPermission\(permissions, \/\^ui\\\./,
    /const signalControlsUi = matches\(signalText,/,
    /const declaredNetwork = hasPermission\(permissions, \/\^network\\\./,
    /const signalNetwork =[\s\S]*executionTypes\.has\("http"\)/,
    /const declaredMessages = hasPermission\(permissions, \/\^messaging\\\./,
    /const signalMessages = matches\(signalText,/,
    /tool\.supportsDryRun/,
    /tool\.rollbackHint/
  ]) {
    assertMatches(skillsView, pattern, "Product Manifest card boundary");
  }
}

function assertBuiltRendererExists() {
  const indexPath = path.join(desktopRoot, "dist", "renderer", "index.html");
  assert.ok(
    fs.existsSync(indexPath),
    "renderer preview build is missing; run `npm run build:renderer` before skill manifest UI smoke"
  );
}

async function assertRenderedManifestBoundary() {
  const previewPort = Number(process.env.LENGRVIS_SKILL_MANIFEST_UI_PORT) || await getFreePort();
  const previewUrl = `http://${previewHost}:${previewPort}`;
  const preview = startPreview(previewPort);
  let browser;

  try {
    await waitForPreview(previewUrl);
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error));

    const counters = { skillsRequests: 0 };
    await installApiMocks(page, counters);
    await page.goto(`${previewUrl}/?view=settings`, { waitUntil: "domcontentloaded", timeout: 30_000 });

    const skillsPanel = page.locator(".panel--skills");
    await skillsPanel.waitFor({ timeout: 30_000 });
    await page.getByText("product-manifest-showcase").waitFor({ timeout: 30_000 });
    await page.locator('[aria-label="Manifest 声明权限与文本推断信号"]').first().waitFor({ timeout: 30_000 });
    await page.getByText(/权限以 Manifest 声明为准，文本提示只作提醒/).first().waitFor({ timeout: 30_000 });

    const panelText = await skillsPanel.innerText();
    for (const source of ["Manifest 声明", "文本提示", "安全检查", "未声明"]) {
      assert.match(panelText, new RegExp(source), `rendered Product Manifest source should include ${source}`);
    }
    assert.match(panelText, /读文件/, "rendered Product Manifest cards should include file-read capability");
    assert.match(panelText, /操作 UI/, "rendered Product Manifest cards should include UI capability");
    assert.match(panelText, /Preview/, "rendered Product Manifest cards should include preview boundary");
    assert.match(panelText, /Rollback\/Handoff/, "rendered Product Manifest cards should include rollback boundary");
    assert.ok(counters.skillsRequests >= 1, "SkillsView should load /api/skills from the mocked backend");
    assert.equal(pageErrors.length, 0, `rendered Product Manifest UI had page errors: ${pageErrors.map(String).join("; ")}`);

    fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
    await skillsPanel.scrollIntoViewIfNeeded();
    await skillsPanel.screenshot({ path: screenshotPath });
    await page.close();
  } finally {
    if (browser) await browser.close();
    await stopProcess(preview);
  }
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

async function installApiMocks(page, counters) {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== backendOrigin) {
      await route.continue();
      return;
    }

    const method = request.method().toUpperCase();
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
    if (url.pathname === "/api/settings") return json(backendSettings());
    if (url.pathname === "/api/settings/llm/health") return json({ active: { available: true, provider: "smoke", model: "smoke" }, retry: {} });
    if (url.pathname === "/api/settings/llm/cost-summary") return json({ by_model: [] });
    if (url.pathname === "/api/settings/local-llm/health") return json({ available: false, probe_order: ["ollama"], error: "smoke offline" });
    if (url.pathname === "/api/settings/local-model/setup-plan") return json(localModelSetupPlan());
    if (url.pathname === "/api/settings/onnx/status") return json({ available: false, kind: "onnx", errors: [] });
    if (url.pathname === "/api/settings/permission-policy") return json({ ok: true, policy: { version: "smoke", rules: [] } });
    if (url.pathname === "/api/settings/ollama/status") return json({ installed: true, running: true, models: ["qwen2.5:3b-instruct"], has_recommended: true, recommended_model: "qwen2.5:3b-instruct" });
    if (url.pathname === "/api/context/usage") return json({});
    if (url.pathname === "/api/audit" || url.pathname === "/api/audit/logs") return json([]);
    if (url.pathname === "/api/chat/proactive-suggestions") return json([]);
    if (url.pathname === "/api/browser/sessions") return json({ ok: true, sessions: [] });
    if (url.pathname === "/api/system/info") return json({ system: "Windows", platform: "win32", machine: "x64" });
    if (url.pathname === "/api/system/diagnostics") return json(systemDiagnostics());
    if (url.pathname === "/api/system/processes") return json({ processes: [], count: 0 });
    if (url.pathname === "/api/system/startup-items") return json({ startup_items: [], count: 0 });
    if (url.pathname === "/api/apps") return json({ apps: [] });
    if (url.pathname === "/api/pair/devices") return json({ devices: [] });
    if (url.pathname === "/api/skills" && method === "GET") {
      counters.skillsRequests += 1;
      return json(skillCatalog());
    }

    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: `unmocked skill manifest UI smoke endpoint: ${method} ${url.pathname}` })
    });
  });
}

function backendSettings() {
  return {
    base_url: backendOrigin,
    allowed_directories: ["C:\\Users\\Smoke\\Documents"],
    mode: "efficiency",
    allow_cloud_context: false,
    allow_file_content_upload: false,
    permission_mode: "default",
    mcp_servers: []
  };
}

function localModelSetupPlan() {
  return {
    ready: false,
    can_install: false,
    model: "qwen2.5:3b-instruct",
    readiness: {
      can_install: false,
      recommended_model: "qwen2.5:3b-instruct",
      reason: "smoke fixture keeps local model setup inactive"
    },
    steps: []
  };
}

function systemDiagnostics() {
  return {
    info: {},
    disks: [],
    network: {},
    battery: null,
    top_processes: [],
    startup_items: [],
    suggestions: [],
    diagnostic_scope: "local_only",
    local_paths: {
      log_dirs: ["C:\\Users\\Smoke\\AppData\\Local\\Lengrvis\\logs"]
    }
  };
}

function skillCatalog() {
  const skills = [
    productManifestShowcaseSkill(),
    textSignalOnlySkill(),
    guardrailOnlySkill(),
    quietUnspecifiedSkill()
  ];
  return {
    skills,
    count: skills.length,
    directories: ["C:\\Users\\Smoke\\Skills"],
    install_directory: "C:\\Users\\Smoke\\Skills"
  };
}

function productManifestShowcaseSkill() {
  const permissions = [
    "filesystem.read",
    "filesystem.write",
    "filesystem.delete",
    "ui.control",
    "network.external",
    "messaging.send"
  ];
  return {
    name: "product-manifest-showcase",
    version: "0.1.0",
    agent_owner: "AppAgent",
    risk: "R3_DESTRUCTIVE_OR_SYSTEM",
    root: "C:\\Users\\Smoke\\Skills\\product_manifest_showcase",
    manifest_path: "C:\\Users\\Smoke\\Skills\\product_manifest_showcase\\skill.yaml",
    status: "ready",
    tools: [
      {
        name: "skill.product_manifest.showcase",
        description: "Preview a sample App Skill that reads and writes files, controls UI, calls a network endpoint, sends a message, deletes generated data, and shows rollback or handoff boundaries.",
        agent_owner: "AppAgent",
        risk: "R3_DESTRUCTIVE_OR_SYSTEM",
        permissions,
        input_schema: {},
        execution_type: "python",
        entry: "handlers/intent.py",
        supports_dry_run: true,
        requires_authorized_path: true,
        rollback_hint: "Preview must list each file, UI, network, messaging, and delete operation before approval; rollback deletes generated outputs or must hand off to the user."
      }
    ],
    safety: { ok: true, issues: [] },
    error: ""
  };
}

function textSignalOnlySkill() {
  return {
    name: "text-signal-only-skill",
    version: "0.1.0",
    agent_owner: "DocumentAgent",
    risk: "R1_READ_ONLY",
    root: "C:\\Users\\Smoke\\Skills\\text_signal_only",
    manifest_path: "C:\\Users\\Smoke\\Skills\\text_signal_only\\skill.yaml",
    status: "ready",
    tools: [
      {
        name: "skill.text_signal.inspect",
        description: "Read a local document path and open an app window for inspection without declaring manifest permissions.",
        agent_owner: "DocumentAgent",
        risk: "R1_READ_ONLY",
        permissions: [],
        input_schema: {},
        execution_type: "python",
        entry: "handlers/inspect.py",
        supports_dry_run: false,
        requires_authorized_path: false,
        rollback_hint: ""
      }
    ],
    safety: { ok: true, issues: [] },
    error: ""
  };
}

function guardrailOnlySkill() {
  return {
    name: "guardrail-review-skill",
    version: "0.1.0",
    agent_owner: "OpsAgent",
    risk: "R3_DESTRUCTIVE_OR_SYSTEM",
    root: "C:\\Users\\Smoke\\Skills\\guardrail_review",
    manifest_path: "C:\\Users\\Smoke\\Skills\\guardrail_review\\skill.yaml",
    status: "ready",
    tools: [
      {
        name: "skill.guardrail.review",
        description: "Review a high-risk operation boundary for approval readiness.",
        agent_owner: "OpsAgent",
        risk: "R3_DESTRUCTIVE_OR_SYSTEM",
        permissions: [],
        input_schema: {},
        execution_type: "python",
        entry: "handlers/review.py",
        supports_dry_run: false,
        requires_authorized_path: false,
        rollback_hint: ""
      }
    ],
    safety: {
      ok: false,
      issues: [
        {
          severity: "warning",
          location: "skill.guardrail.review",
          message: "R3 operation must declare supports_dry_run preview before execution."
        }
      ]
    },
    error: ""
  };
}

function quietUnspecifiedSkill() {
  return {
    name: "quiet-helper",
    version: "0.1.0",
    agent_owner: "UtilityAgent",
    risk: "R0_METADATA_ONLY",
    root: "C:\\Users\\Smoke\\Skills\\quiet_helper",
    manifest_path: "C:\\Users\\Smoke\\Skills\\quiet_helper\\skill.yaml",
    status: "ready",
    tools: [
      {
        name: "skill.quiet.noop",
        description: "Compute a small in-memory status summary.",
        agent_owner: "UtilityAgent",
        risk: "R0_METADATA_ONLY",
        permissions: [],
        input_schema: {},
        execution_type: "python",
        entry: "handlers/noop.py",
        supports_dry_run: false,
        requires_authorized_path: false,
        rollback_hint: ""
      }
    ],
    safety: { ok: true, issues: [] },
    error: ""
  };
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
