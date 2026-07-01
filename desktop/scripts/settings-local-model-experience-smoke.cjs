const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { chromium } = require("@playwright/test");

const desktopRoot = path.resolve(__dirname, "..");
const workspaceRoot = path.resolve(desktopRoot, "..");
const previewHost = "127.0.0.1";
const backendOrigin = "http://127.0.0.1:8000";
const previewReadyTimeoutMs = 30_000;
const screenshotPaths = {
  desktop: path.join(workspaceRoot, ".tmp", "qa-evidence", "settings-local-model-experience-smoke-desktop.png"),
  desktopSetup: path.join(workspaceRoot, ".tmp", "qa-evidence", "settings-local-model-experience-smoke-desktop-setup.png"),
  narrow: path.join(workspaceRoot, ".tmp", "qa-evidence", "settings-local-model-experience-smoke-narrow.png"),
  narrowSetup: path.join(workspaceRoot, ".tmp", "qa-evidence", "settings-local-model-experience-smoke-narrow-setup.png")
};
const smokeViewports = [
  {
    name: "desktop",
    width: 1366,
    height: 900,
    minBoundaryCardWidth: 220,
    maxBoundaryCardHeight: 360,
    minPrivacyStepWidth: 140,
    minRepairActionWidth: 150,
    setupScreenshotPath: screenshotPaths.desktopSetup
  },
  {
    name: "narrow",
    width: 900,
    height: 900,
    minBoundaryCardWidth: 220,
    maxBoundaryCardHeight: 400,
    minPrivacyStepWidth: 180,
    minRepairActionWidth: 150,
    setupScreenshotPath: screenshotPaths.narrowSetup
  }
];
const layoutTolerancePx = 1;
const forbiddenSettingsPageContractTerms = [
  { label: "Ollama", pattern: /\bOllama\b/ },
  { label: "LM Studio", pattern: /LM Studio/ },
  { label: "llama.cpp", pattern: /llama\.cpp/ },
  { label: "manifest", pattern: /\bmanifest\b/i },
  { label: "runtime", pattern: /\bruntime\b/i },
  { label: "backend", pattern: /\bbackend\b/i },
  { label: "Privacy mode requires", pattern: /Privacy mode requires/ },
  { label: "Install Ollama runtime", pattern: /Install Ollama runtime/ },
  { label: "Hardware readiness", pattern: /Hardware readiness/ },
  { label: "Start local service", pattern: /Start local service/ },
  { label: "Download recommended model", pattern: /Download recommended model/ },
  { label: "API key", pattern: /API key/ },
  { label: "Provider", pattern: /\bProvider\b/ },
  { label: "embedding", pattern: /\bembedding\b/i },
  { label: "public_safe=false", pattern: /public_safe(?:\s*=\s*false|_false)?/ },
  { label: "template_status", pattern: /template_status/ }
];

const localModelReadiness = {
  can_install: true,
  recommended_model: "qwen2.5:3b",
  reason: "这台电脑满足 Qwen2.5 3B 的推荐条件，但本地模型尚未安装。",
  checks: [
    { key: "memory", label: "内存", ok: true, actual: "16 GB", required: "8 GB" },
    { key: "disk", label: "磁盘", ok: true, actual: "64 GB", required: "12 GB" },
    { key: "cpu", label: "CPU", ok: true, actual: "8 核", required: "4 核" }
  ],
  memory_total_bytes: 16 * 1024 ** 3,
  disk_free_bytes: 64 * 1024 ** 3,
  cpu_logical_cores: 8,
  gpu_summary: "未检测到 GPU；CPU 路径可用"
};

const localLlmHealth = {
  available: false,
  selected_backend: null,
  probe_order: ["onnx", "ollama", "lmstudio", "llamacpp"],
  error: "Privacy mode requires a reachable local LLM backend. Start Ollama, LM Studio, or a llama.cpp-compatible OpenAI server, then retry.",
  readiness: localModelReadiness
};

const cleanMachineSetupPlan = {
  ready: false,
  can_install: true,
  model: "qwen2.5:3b",
  readiness: localModelReadiness,
  installed: false,
  running: false,
  models: [],
  has_model: false,
  runtime_source: "missing",
  bundled_runtime_available: false,
  bundled_runtime_path: "",
  bundled_models_available: false,
  bundled_models_path: "",
  bundled_model_available: false,
  bundled_model_configured: false,
  bundle_manifest: { present: false },
  next_action: "install_runtime",
  repair_action: {
    code: "install_runtime",
    label: "Install Ollama runtime",
    detail: "Use one-click setup to install Ollama, start the local service, and prepare qwen2.5:3b."
  },
  verification: {
    ready: false,
    next_action: "install_runtime",
    paths_redacted: true,
    privacy_fallback: "local_only_until_ready",
    runtime: {
      checked: true,
      found: false,
      source: "missing",
      path: ""
    },
    server: {
      checked: false,
      responding: false
    },
    model: {
      required: "qwen2.5:3b",
      listed: false,
      models_seen: []
    },
    bundle: {
      runtime_found: false,
      model_proven: false,
      model_configured: false,
      manifest_present: false,
      manifest_valid: false,
      manifest_model_matches: false,
      paths: ""
    }
  },
  evidence: [
    {
      key: "hardware",
      ok: true,
      detail: "Memory, disk, and CPU are ready for qwen2.5:3b."
    },
    {
      key: "runtime",
      ok: false,
      value: "missing",
      path: "",
      detail: "No Ollama runtime executable was found."
    },
    {
      key: "bundle_manifest",
      ok: false,
      path: "",
      value: "",
      detail: "No Ollama bundle manifest was found."
    },
    {
      key: "bundled_model",
      ok: false,
      models_path: "",
      model_manifest_path: "",
      configured: false,
      detail: "Bundled qwen2.5:3b is not proven available; missing bundled runtime, bundled models directory, model manifest, valid bundle manifest."
    }
  ],
  steps: [
    {
      key: "hardware",
      label: "Hardware readiness",
      state: "done",
      detail: "Memory, disk, and CPU are ready for qwen2.5:3b."
    },
    {
      key: "runtime",
      label: "Install Ollama runtime",
      state: "current",
      detail: "Ollama is not installed yet; Lengrvis can install it automatically."
    },
    {
      key: "server",
      label: "Start local service",
      state: "pending",
      detail: "After installation Lengrvis starts the local AI service."
    },
    {
      key: "model",
      label: "Download recommended model",
      state: "pending",
      detail: "Download qwen2.5:3b before privacy tasks can use a local model."
    }
  ]
};

const backendSettings = {
  base_url: backendOrigin,
  provider_name: "openai_compatible",
  model: "gpt-4o-mini",
  review_model: "",
  wire_api: "chat_completions",
  allowed_directories: ["C:\\Users\\Smoke\\Documents"],
  mode: "privacy",
  permission_mode: "default",
  allow_cloud_context: false,
  allow_file_content_upload: false,
  allow_browser_network: false,
  remote_desktop_enabled: false,
  mcp_servers: []
};

const backendDiagnostics = {
  info: {
    memory_total: 16 * 1024 * 1024 * 1024,
    memory_available: 10 * 1024 * 1024 * 1024
  },
  disks: [],
  network: {},
  battery: null,
  top_processes: [],
  startup_items: [],
  suggestions: ["No critical system issue detected from read-only diagnostics."],
  diagnostic_scope: "local_only",
  product: {
    name: "Lengrvis",
    version: "0.1.0-smoke"
  }
};

async function main() {
  runSourceAssertions();
  assertCleanMachineSetupPlanContract();
  assertBuiltRendererExists();

  const previewPort = Number(process.env.LENGRVIS_SETTINGS_LOCAL_MODEL_PORT) || await getFreePort();
  const previewUrl = `http://${previewHost}:${previewPort}`;
  const preview = startPreview(previewPort);
  let browser;

  try {
    await waitForPreview(previewUrl);
    browser = await chromium.launch();
    for (const viewport of smokeViewports) {
      await runViewportScenario(browser, previewUrl, viewport);
    }
    await runEfficiencyModeIntroScenario(browser, previewUrl);
    await runSensitiveLocalModelErrorScenario(browser, previewUrl);
  } finally {
    if (browser) await browser.close();
    await stopProcess(preview);
  }

  console.log(
    `settings local model experience smoke passed; screenshots: ${evidenceScreenshotPaths().join(", ")}`
  );
}

function assertCleanMachineSetupPlanContract() {
  assert.equal(cleanMachineSetupPlan.ready, false, "clean machine setup plan must not report local model readiness");
  assert.equal(cleanMachineSetupPlan.runtime_source, "missing", "clean machine setup plan should report missing runtime");
  assert.equal(cleanMachineSetupPlan.bundled_runtime_available, false, "clean machine setup plan must not claim bundled runtime");
  assert.equal(cleanMachineSetupPlan.bundled_models_available, false, "clean machine setup plan must not claim bundled model store");
  assert.equal(cleanMachineSetupPlan.bundled_model_available, false, "clean machine setup plan must not claim default offline model availability");
  assert.deepEqual(cleanMachineSetupPlan.bundle_manifest, { present: false }, "clean machine setup plan needs explicit missing manifest evidence");
  assert.equal(cleanMachineSetupPlan.next_action, "install_runtime", "clean machine next action should install the local runtime");
  assert.equal(cleanMachineSetupPlan.repair_action.code, "install_runtime", "clean machine repair action should install runtime");
  assert.equal(cleanMachineSetupPlan.verification.ready, false, "clean machine verification must not report readiness");
  assert.equal(cleanMachineSetupPlan.verification.next_action, "install_runtime", "clean machine verification should mirror next action");
  assert.equal(cleanMachineSetupPlan.verification.paths_redacted, true, "clean machine verification should not expose local paths");
  assert.equal(cleanMachineSetupPlan.verification.runtime.found, false, "clean machine verification should report no runtime");
  assert.equal(cleanMachineSetupPlan.verification.model.listed, false, "clean machine verification should report no listed model");
  assert.equal(cleanMachineSetupPlan.verification.bundle.model_proven, false, "clean machine verification should not claim bundled model proof");
  assert.ok(
    cleanMachineSetupPlan.evidence.some((item) => item.key === "bundle_manifest" && item.ok === false),
    "clean machine setup plan should include missing bundle manifest evidence"
  );
  assert.ok(
    cleanMachineSetupPlan.evidence.some((item) => item.key === "bundled_model" && item.ok === false),
    "clean machine setup plan should include missing bundled model evidence"
  );
}

async function runViewportScenario(browser, previewUrl, viewport) {
  const page = await browser.newPage({
    viewport: { width: viewport.width, height: viewport.height }
  });
  page.on("pageerror", (error) => {
    throw error;
  });

  try {
    const counters = {
      localHealthRequests: 0,
      setupPlanRequests: 0,
      installRequests: 0,
      hardwareRequests: 0
    };
    await installDesktopBridgeMocks(page);
    await installApiMocks(page, counters);

    await page.goto(`${previewUrl}/?view=settings`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForSelector("[aria-label='模型边界']", { timeout: 30_000 });
    await waitForCounter(() => counters.localHealthRequests >= 1, `${viewport.name} local LLM health request`);
    await waitForCounter(() => counters.setupPlanRequests >= 1, `${viewport.name} local model setup plan request`);
    await waitForCounter(() => counters.hardwareRequests >= 1, `${viewport.name} hardware readiness request`);

    await assertModelBoundary(page);
    await assertCleanMachinePrivacyPath(page);
    await assertNoInternalSettingsContractText(page, `${viewport.name} clean-machine settings page`);
    await captureEvidence(page, viewport);
    await assertResponsiveLocalModelLayout(page, viewport);

    assert.equal(
      counters.installRequests,
      0,
      `${viewport.name} readiness smoke must not start model installation without a user click`
    );
    await assertOneClickLocalModelInstallPath(page, counters, viewport);
    await assertNoInternalSettingsContractText(page, `${viewport.name} post-install settings page`);
  } finally {
    await page.close();
  }
}

async function runEfficiencyModeIntroScenario(browser, previewUrl) {
  const context = await browser.newContext({
    viewport: { width: 900, height: 760 }
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => {
    throw error;
  });

  try {
    const counters = {
      localHealthRequests: 0,
      setupPlanRequests: 0,
      installRequests: 0,
      hardwareRequests: 0
    };
    await installDesktopBridgeMocks(page);
    await installApiMocks(page, counters, {
      settings: {
        ...backendSettings,
        mode: "efficiency",
        allow_cloud_context: true,
        allow_file_content_upload: true
      }
    });

    await page.goto(`${previewUrl}/?view=settings`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    const privacyPanel = page.locator(".settings-privacy-anchor .privacy-readiness").first();
    await privacyPanel.waitFor({ timeout: 15_000 });

    const privacyText = await privacyPanel.innerText();
    assert.match(privacyText, /开启隐私模式/, "efficiency mode should offer a first-step privacy switch");
    assert.match(
      privacyText,
      /开启隐私模式只会关闭云端辅助并检查本地 AI/,
      "efficiency mode note should not imply the switch immediately installs local AI"
    );
    assert.match(privacyText, /下一步再按提示准备本地模型/, "efficiency mode note should stage local model setup as a next step");
    assert.match(privacyText, /不会静默回退云端/, "efficiency mode should keep the no-silent-cloud-fallback boundary visible");
    assert.doesNotMatch(
      privacyText,
      /主按钮会按顺序安装本地 AI 应用、启动本地服务，并下载或启用推荐模型/,
      "efficiency mode should not describe the privacy switch as an install/download action"
    );
    await assertNoInternalSettingsContractText(page, "efficiency settings page");
    assert.equal(counters.installRequests, 0, "efficiency mode intro must not start local model installation");
  } finally {
    await context.close();
  }
}

async function runSensitiveLocalModelErrorScenario(browser, previewUrl) {
  const context = await browser.newContext({
    viewport: { width: 900, height: 760 }
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => {
    throw error;
  });

  const sensitiveMessage = "安装失败：C:\\Users\\Smoke\\secret-models\\qwen.bin token=local-secret-123456 https://models.example.invalid/qwen";

  try {
    const counters = {
      localHealthRequests: 0,
      setupPlanRequests: 0,
      installRequests: 0,
      hardwareRequests: 0
    };
    await installDesktopBridgeMocks(page);
    await installApiMocks(page, counters, {
      localHealth: {
        ...localLlmHealth,
        error: sensitiveMessage
      },
      setupPlan: {
        ...cleanMachineSetupPlan,
        verification: undefined,
        repair_action: {
          ...cleanMachineSetupPlan.repair_action,
          detail: sensitiveMessage
        }
      },
      installLocalModelResponse: {
        ok: false,
        error: sensitiveMessage,
        message: sensitiveMessage
      }
    });

    await page.goto(`${previewUrl}/?view=settings`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForSelector("[aria-label='模型边界']", { timeout: 30_000 });
    await waitForCounter(() => counters.setupPlanRequests >= 1, "sensitive setup plan request");
    await waitForPrivacyReadinessToSettle(page);

    const beforeClickText = await page.locator("body").innerText();
    assert.match(beforeClickText, /本机路径待隐藏/, "missing verification must not claim paths are hidden");
    assert.doesNotMatch(beforeClickText, /本机路径已隐藏/, "missing verification must fail closed on path redaction");
    assert.doesNotMatch(beforeClickText, /本地 AI 已验证可用/, "missing verification must not claim verified local AI readiness");
    await assertNoSensitiveLocalModelText(page, "before install");

    const installButton = page.getByRole("button", { name: /一键安装本地 AI 应用并准备 qwen2\.5:3b/ }).first();
    await installButton.click();
    await waitForCounter(() => counters.installRequests === 1, "sensitive install request");
    await page.getByText(/安装任务启动失败|安装失败，请重新检查或重试/).first().waitFor({ timeout: 5_000 });

    const afterClickText = await page.locator("body").innerText();
    assert.match(afterClickText, /安装任务启动失败|安装失败，请重新检查或重试/, "sensitive install failures should use safe fallback copy");
    await assertNoSensitiveLocalModelText(page, "after install failure");
    await assertNoInternalSettingsContractText(page, "sensitive local-model error page");
  } finally {
    await context.close();
  }
}

async function assertNoSensitiveLocalModelText(page, label) {
  const patternSource = "secret-models|local-secret-123456|models\\.example\\.invalid";
  const matches = await page.evaluate((source) => {
    const pattern = new RegExp(source);
    return [...document.querySelectorAll("body *")]
      .filter((element) => {
        const text = element.textContent || "";
        if (!pattern.test(text)) return false;
        return [...element.children].every((child) => !pattern.test(child.textContent || ""));
      })
      .map((element) => ({
        tag: element.tagName.toLowerCase(),
        className: element.getAttribute("class") || "",
        testId: element.getAttribute("data-testid") || "",
        text: (element.textContent || "").replace(/\s+/g, " ").slice(0, 260)
      }))
      .slice(0, 5);
  }, patternSource);
  assert.deepEqual(matches, [], `${label} must not render sensitive local model text: ${JSON.stringify(matches, null, 2)}`);
}

function runSourceAssertions() {
  const settingsSource = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "components", "SettingsPanel.tsx"), "utf8");
  const settingsModuleSources = [
    settingsSource,
    ...[
      "LocalModelSettings.tsx",
      "LocalModelInstaller.tsx",
      "PrivacyReadinessPanel.tsx",
      "PrivacyModeEntry.tsx",
      "OllamaSetup.tsx"
    ].map((fileName) => {
      const modulePath = path.join(desktopRoot, "src", "renderer", "components", "settings", fileName);
      return fs.existsSync(modulePath) ? fs.readFileSync(modulePath, "utf8") : "";
    })
  ]
    .join("\n");
  const apiClientSource = fs.readFileSync(path.join(desktopRoot, "src", "renderer", "lib", "api", "client.ts"), "utf8");

  assert.match(
    apiClientSource,
    /installLocalModel\(request: LocalModelInstallRequest[\s\S]*window\.lengrvis\?\.localModel[\s\S]*\/api\/settings\/install-local-model/,
    "local model install should use the explicit desktop bridge before direct backend fallback"
  );
  assert.match(apiClientSource, /installOllama\(\)[\s\S]*\/api\/settings\/ollama\/install/, "Ollama install helper should be explicit");
  assert.match(apiClientSource, /startOllama\(\)[\s\S]*\/api\/settings\/ollama\/start/, "Ollama start helper should be explicit");
  assert.match(apiClientSource, /pullOllama\(model\?: string\)[\s\S]*\/api\/settings\/ollama\/pull/, "Ollama pull helper should be explicit");

  assert.match(settingsModuleSources, /api\.installLocalModel\(\{ model \}\)/, "settings installer should call the apiClient one-click install method");
  assert.doesNotMatch(
    settingsModuleSources,
    /api\.request<[\s\S]*\/api\/settings\/install-local-model/,
    "settings installer should not post install-local-model through the generic renderer API"
  );

  for (const expectedText of [
    "一键安装本地 AI 应用并准备",
    "一键启动本地 AI 服务并启用随包模型",
    "一键启动本地 AI 服务并检查模型",
    "一键联网下载",
    "安装本地 AI 应用、启动服务、准备模型",
    "安装本地 AI 应用、启动本地服务，并联网下载或启用推荐模型",
    "主程序安装包不内置大模型",
    "模型不包含在主程序安装包内"
  ]) {
    assert.ok(settingsModuleSources.includes(expectedText), `settings page should expose clear next step copy: ${expectedText}`);
  }

  for (const expectedText of [
    "allowCloudContext: false",
    "allowFileContentUpload: false",
    "隐私模式已开启，本地 AI 尚未就绪",
    "开启隐私模式只会关闭云端辅助并检查本地 AI",
    "当前未检测到可用的本地 AI 应用或随包模型",
    "请按下方“隐私模式开箱检查”的主按钮继续准备",
    "完成前不会上传文件",
    "不会把缺失的离线模型当作已可用",
    "不会静默回退云端",
    "隐私模式失败时不会静默回退云端"
  ]) {
    assert.ok(settingsModuleSources.includes(expectedText), `privacy mode fallback behavior should be visible and testable: ${expectedText}`);
  }
}

function assertBuiltRendererExists() {
  const indexPath = path.join(desktopRoot, "dist", "renderer", "index.html");
  assert.ok(
    fs.existsSync(indexPath),
    "renderer preview build is missing; run `npm run build:renderer` before settings local model smoke"
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

function evidenceScreenshotPaths() {
  return smokeViewports.flatMap((viewport) => {
    const paths = [screenshotPaths[viewport.name]];
    if (viewport.setupScreenshotPath) paths.push(viewport.setupScreenshotPath);
    return paths.filter((item) => typeof item === "string");
  });
}

async function assertModelBoundary(page) {
  const boundary = page.getByLabel("模型边界");
  const boundaryText = await boundary.innerText();

  assert.match(boundaryText, /快速/, "model boundary should show the quick mode option");
  assert.match(boundaryText, /智能混合/, "model boundary should show the hybrid mode option");
  assert.match(boundaryText, /隐私/, "model boundary should show the privacy mode option");
  assert.match(boundaryText, /推荐模型/, "model boundary should expose recommended model facts");
  assert.match(boundaryText, /qwen2\.5:3b/, "model boundary should name the recommended local model");
  assert.match(boundaryText, /模型大小/, "model boundary should expose model size");
  assert.match(boundaryText, /约 2-3 GB/, "model boundary should make the local model download size visible");
  assert.match(boundaryText, /硬件状态/, "model boundary should expose hardware status");
  assert.match(boundaryText, /CPU 路径可用|未检测到 GPU/, "model boundary should show hardware readiness without pretending GPU acceleration exists");
  assert.match(boundaryText, /速度预估/, "model boundary should expose speed expectations");
  assert.match(boundaryText, /待本地 AI 就绪/, "privacy speed should not claim the clean machine is ready");
  assert.match(boundaryText, /失败修复：下一步安装本地 AI 应用/, "privacy card should point to the first repair action");
  assert.match(boundaryText, /隐私失败不自动回云端/, "privacy boundary should make cloud fallback explicit");
  assert.doesNotMatch(boundaryText, /默认离线模型.*(可用|已就绪)/, "model boundary must not claim a default offline model is already available");
}

async function assertCleanMachinePrivacyPath(page) {
  const privacyPanel = page.locator(".local-model-installer .privacy-readiness").first();
  await privacyPanel.waitFor({ timeout: 15_000 });
  await waitForPrivacyReadinessToSettle(page);
  const privacyText = await privacyPanel.innerText();

  assert.match(privacyText, /隐私模式开箱检查/, "privacy readiness panel should render for a clean machine");
  assert.match(privacyText, /待配置|检查中/, "privacy readiness should be framed as setup, not ready");
  assert.match(privacyText, /这台电脑条件已通过，下一步安装本地 AI 应用/, "clean machine should get a concrete next step");
  assert.match(privacyText, /检查电脑条件/, "privacy path should show the hardware step");
  assert.match(privacyText, /安装本地 AI 应用/, "privacy path should show the local app step");
  assert.match(privacyText, /启动本地 AI 服务/, "privacy path should show the service step");
  assert.match(privacyText, /联网下载推荐模型/, "privacy path should show the model step");
  assert.match(privacyText, /主程序安装包不内置大模型/, "privacy path should distinguish app size from model download size");
  assert.match(privacyText, /模型不包含在主程序安装包内|不计入主程序安装包体积/, "privacy path should say model download is outside the main exe");
  assert.match(privacyText, /一键安装本地 AI 应用并准备 qwen2\.5:3b/, "clean machine should expose a one-click install path");
  assert.match(privacyText, /本机路径已隐藏/, "privacy path should show backend verification redaction status");
  assert.match(privacyText, /下一步：安装本地 AI 应用/, "privacy path should expose localized repair action copy");
  assert.match(privacyText, /下一步说明：当前未检测到可用的本地 AI 应用或随包模型/, "privacy path should explain why clean-machine local setup is blocked");
  assert.match(privacyText, /不会把缺失的离线模型当作已可用/, "privacy path must fail closed on missing bundled or offline model evidence");
  assert.match(privacyText, /4 个检查项，3 个待处理/, "privacy path should consume backend evidence summary");
  assert.match(privacyText, /重新检查/, "privacy path should expose a retry/check action");
  assert.match(privacyText, /不会静默回退云端/, "privacy path should make the no-silent-cloud-fallback boundary visible");
  assert.doesNotMatch(privacyText, /隐私模式已就绪|本地 AI 已就绪|可本地处理/, "clean machine privacy panel must not claim local model readiness");

  const localStatusText = await page.locator(".local-llm-status").first().innerText();
  assert.match(localStatusText, /本地 AI 需要配置/, "local AI status notice should identify the setup state");
  assert.match(
    localStatusText,
    /请按下方“隐私模式开箱检查”的主按钮继续准备/,
    "local AI status notice should point beginners to the existing next-step button"
  );
  assert.match(localStatusText, /完成前不会上传文件/, "local AI status notice should make the privacy boundary visible");
  assert.match(localStatusText, /不会静默回退云端/, "local AI status notice should keep the no-silent-cloud-fallback boundary visible");

  const installButton = page.getByRole("button", { name: /一键安装本地 AI 应用并准备 qwen2\.5:3b/ }).first();
  assert.equal(await installButton.isEnabled(), true, "one-click local model setup should be actionable on a clean machine");

  const installer = page.locator(".local-model-installer").first();
  const installerText = await installer.innerText();
  assert.match(installerText, /本地 AI 备选设置/, "settings should expose a fallback local model section");
  assert.match(installerText, /上方按钮会安装本地 AI 应用、启动服务、准备 qwen2\.5:3b/, "fallback section should explain the same one-click sequence");
  assert.match(installerText, /内存[\s\S]*16 GB[\s\S]*需要 8 GB/, "readiness should show memory requirements");
  assert.match(installerText, /磁盘[\s\S]*64 GB[\s\S]*需要 12 GB/, "readiness should show disk requirements");
  assert.match(installerText, /CPU[\s\S]*8 核[\s\S]*需要 4 核/, "readiness should show CPU requirements");
  assert.match(installerText, /图形加速：未检测到 GPU；CPU 路径可用/, "readiness should show hardware/speed context");
  assert.match(installerText, /安装所选模型/, "fallback install action should be visible");
  assert.match(installerText, /待安装/, "clean machine should not be marked as installed");
  assert.match(installerText, /选择模型后即可准备到这台电脑/, "initial progress copy should tell the user what happens next");

  const manualButton = page.getByRole("button", { name: /安装所选模型/ }).first();
  assert.equal(await manualButton.isEnabled(), true, "manual local model install should also be actionable");

  const fullPageText = await page.locator("body").innerText();
  assert.doesNotMatch(fullPageText, /[^不]会静默回退云端/, "page must not imply privacy mode silently falls back to cloud");
  assert.doesNotMatch(fullPageText, /默认离线模型.*(可用|已就绪)/, "page must not claim a default offline model is ready");
}

async function assertOneClickLocalModelInstallPath(page, counters, viewport) {
  const installButton = page.getByRole("button", { name: /一键安装本地 AI 应用并准备 qwen2\.5:3b/ }).first();
  await installButton.click();
  await waitForCounter(() => counters.installRequests === 1, `${viewport.name} one-click local model install request`);

  const progress = page.locator(".local-model-progress").first();
  await expectText(progress, /qwen2\.5:3b 已就绪；隐私模式失败时不会静默回退云端。/, "one-click progress should end with a local-only privacy boundary");
  await expectText(progress, /100%/, "one-click progress should reach completion in the mocked clean-machine flow");

  const bodyText = await page.locator("body").innerText();
  assert.doesNotMatch(bodyText, /[^不]会静默回退云端/, "one-click completion must not imply silent cloud fallback");
  assert.doesNotMatch(bodyText, /默认离线模型.*(可用|已就绪)/, "one-click completion must not claim a default bundled model is proven ready");
}

async function assertNoInternalSettingsContractText(page, label) {
  const bodyText = await page.locator("body").innerText();
  for (const forbidden of forbiddenSettingsPageContractTerms) {
    assert.doesNotMatch(
      bodyText,
      forbidden.pattern,
      `${label} must not expose internal or English contract text: ${forbidden.label}`
    );
  }
}

async function waitForPrivacyReadinessToSettle(page) {
  await page.waitForFunction(() => {
    const panel = document.querySelector(".local-model-installer .privacy-readiness");
    const text = panel?.textContent || "";
    return (
      /下一步安装本地 AI 应用/.test(text) ||
      /一键安装本地 AI 应用/.test(text) ||
      /条件不足/.test(text) ||
      /需要处理/.test(text)
    );
  }, null, { timeout: 10_000 });
}

async function expectText(locator, pattern, message) {
  const deadline = Date.now() + 10_000;
  let text = "";
  while (Date.now() < deadline) {
    text = await locator.innerText().catch(() => "");
    if (pattern.test(text)) return;
    await delay(100);
  }
  assert.match(text, pattern, message);
}

async function assertResponsiveLocalModelLayout(page, viewport) {
  await assertNoDocumentOverflow(page, viewport, "initial");
  await assertSettingsPanelsDoNotOverlap(page, viewport);
  await assertModelBoundaryCardLayout(page, viewport);
  await assertModelBoundaryDetailedLayout(page, viewport);
  await assertLocalModelSetupLayout(page, viewport);
  await assertLocalModelSetupDetailedLayout(page, viewport);
  await assertNoDocumentOverflow(page, viewport, "after layout checks");
}

async function assertSettingsPanelsDoNotOverlap(page, viewport) {
  const findings = await page.locator(".detail-grid--settings > .panel").evaluateAll((panels) => {
    const rectFor = (panel, index) => {
      const rect = panel.getBoundingClientRect();
      return {
        label: panel.querySelector("h2")?.textContent?.trim() || `panel-${index + 1}`,
        top: rect.top,
        bottom: rect.bottom,
        left: rect.left,
        right: rect.right
      };
    };
    const overlaps = (a, b) =>
      a.left < b.right - 1 &&
      a.right > b.left + 1 &&
      a.top < b.bottom - 1 &&
      a.bottom > b.top + 1;
    const rects = panels.map(rectFor);
    const result = [];
    for (let index = 0; index < rects.length; index += 1) {
      for (let next = index + 1; next < rects.length; next += 1) {
        if (overlaps(rects[index], rects[next])) {
          result.push(`${rects[index].label} overlaps ${rects[next].label}`);
        }
      }
    }
    return result;
  });
  assert.deepEqual(findings, [], `${viewport.name} settings panels should not overlap each other`);
}

async function assertNoDocumentOverflow(page, viewport, phase) {
  const metrics = await page.evaluate(() => {
    const collect = (label, element) => {
      const style = window.getComputedStyle(element);
      return {
        label,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
        overflowX: style.overflowX
      };
    };
    const selectorTargets = [
      "#root",
      ".settings-privacy-anchor",
      ".model-boundary-profile",
      ".model-boundary-profile__item",
      ".model-boundary-profile__item-head",
      ".model-boundary-profile__item > span",
      ".model-boundary-profile__facts",
      ".model-boundary-profile__facts div",
      ".model-boundary-profile__item em",
      ".local-model-installer",
      ".local-model-installer .privacy-readiness",
      ".local-model-installer .privacy-readiness__head",
      ".local-model-installer .privacy-readiness__steps",
      ".local-model-installer .privacy-step",
      ".local-model-installer .privacy-readiness__actions",
      ".local-model-installer .privacy-readiness__actions .button",
      ".local-model-installer .privacy-readiness__note",
      ".local-model-installer .privacy-bundle-status",
      ".local-model-installer .privacy-bundle-status__item",
      ".local-model-installer__controls",
      ".local-model-installer__button",
      ".local-model-readiness",
      ".local-model-readiness__checks",
      ".local-model-readiness__check",
      ".local-model-progress"
    ];
    return [
      collect("documentElement", document.documentElement),
      collect("body", document.body),
      ...selectorTargets.flatMap((selector) =>
        Array.from(document.querySelectorAll(selector), (element, index) =>
          collect(index === 0 ? selector : `${selector}[${index}]`, element)
        )
      )
    ];
  });

  for (const metric of metrics) {
    assert.ok(
      metric.scrollWidth <= metric.clientWidth + layoutTolerancePx,
      `${viewport.name} ${phase} ${metric.label} should not overflow horizontally: scrollWidth=${metric.scrollWidth}, clientWidth=${metric.clientWidth}`
    );
  }
}

async function assertModelBoundaryCardLayout(page, viewport) {
  await page.getByLabel("模型边界").scrollIntoViewIfNeeded();
  const cardMetrics = await page.locator(".model-boundary-profile__item").evaluateAll((cards) => cards.map((card) => {
    const rect = card.getBoundingClientRect();
    const panelBodyRect = card.closest(".panel__body")?.getBoundingClientRect();
    const summary = card.querySelector(":scope > span");
    const summaryRect = summary ? summary.getBoundingClientRect() : { width: 0, height: 0 };
    return {
      label: card.querySelector("strong")?.textContent?.trim() || "unknown",
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
      panelBodyTop: panelBodyRect?.top ?? 0,
      panelBodyBottom: panelBodyRect?.bottom ?? 0,
      summaryWidth: summaryRect.width,
      summaryHeight: summaryRect.height
    };
  }));

  assert.equal(cardMetrics.length, 3, `${viewport.name} should render three model boundary cards`);
  assertNoFlatRectOverlaps(cardMetrics, `${viewport.name} model boundary cards`);
  for (const card of cardMetrics) {
    assert.ok(
      card.width >= viewport.minBoundaryCardWidth,
      `${viewport.name} model boundary card "${card.label}" is too narrow: ${Math.round(card.width)}px`
    );
    assert.ok(
      card.left >= -1 && card.right <= viewport.width + 1,
      `${viewport.name} model boundary card "${card.label}" is outside viewport: left=${Math.round(card.left)}px, right=${Math.round(card.right)}px`
    );
    assert.ok(
      card.top >= card.panelBodyTop - 1 && card.bottom <= card.panelBodyBottom + 1,
      `${viewport.name} model boundary card "${card.label}" is clipped by the settings panel body: card=${Math.round(card.top)}-${Math.round(card.bottom)}px, body=${Math.round(card.panelBodyTop)}-${Math.round(card.panelBodyBottom)}px`
    );
    assert.ok(
      card.height <= viewport.maxBoundaryCardHeight,
      `${viewport.name} model boundary card "${card.label}" is suspiciously tall: ${Math.round(card.height)}px`
    );
    assert.ok(
      card.summaryWidth >= 120,
      `${viewport.name} model boundary summary "${card.label}" is likely squeezed into vertical text: ${Math.round(card.summaryWidth)}px`
    );
    assert.ok(
      card.summaryHeight <= 96,
      `${viewport.name} model boundary summary "${card.label}" wraps too tall: ${Math.round(card.summaryHeight)}px`
    );
  }
}

async function assertModelBoundaryDetailedLayout(page, viewport) {
  await page.getByLabel("模型边界").scrollIntoViewIfNeeded();
  const overlapFindings = await page.locator(".model-boundary-profile__item").evaluateAll((cards) => {
    const rectFor = (element) => {
      const rect = element.getBoundingClientRect();
      return {
        top: rect.top,
        bottom: rect.bottom,
        left: rect.left,
        right: rect.right,
        width: rect.width,
        height: rect.height,
        text: (element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80)
      };
    };
    const overlaps = (a, b) =>
      a.left < b.right - 1 &&
      a.right > b.left + 1 &&
      a.top < b.bottom - 1 &&
      a.bottom > b.top + 1;
    const findings = [];
    for (const card of cards) {
      const label = card.querySelector("strong")?.textContent?.trim() || "unknown";
      const blocks = Array.from(card.querySelectorAll(":scope > .model-boundary-profile__item-head, :scope > span, :scope > .model-boundary-profile__facts, :scope > em"), rectFor);
      for (let index = 0; index < blocks.length; index += 1) {
        const block = blocks[index];
        if (block.width < 80 || block.height < 8) {
          findings.push(`${label}: cramped block "${block.text}" (${Math.round(block.width)}x${Math.round(block.height)})`);
        }
        for (let next = index + 1; next < blocks.length; next += 1) {
          if (overlaps(block, blocks[next])) {
            findings.push(`${label}: overlapping "${block.text}" and "${blocks[next].text}"`);
          }
        }
      }
    }
    return findings;
  });
  assert.deepEqual(overlapFindings, [], `${viewport.name} model boundary internal layout should not overlap or collapse`);
}

async function assertLocalModelSetupLayout(page, viewport) {
  await page.locator(".local-model-installer").first().scrollIntoViewIfNeeded();

  const stepMetrics = await page.locator(".local-model-installer .privacy-step").evaluateAll((steps) => steps.map((step) => {
    const rect = step.getBoundingClientRect();
    return {
      label: step.querySelector("strong")?.textContent?.trim() || "unknown",
      width: rect.width,
      height: rect.height
    };
  }));
  assert.ok(stepMetrics.length >= 4, `${viewport.name} should render the local model readiness steps`);
  for (const step of stepMetrics) {
    assert.ok(
      step.width >= viewport.minPrivacyStepWidth,
      `${viewport.name} privacy readiness step "${step.label}" is too narrow: ${Math.round(step.width)}px`
    );
    assert.ok(
      step.height <= 180,
      `${viewport.name} privacy readiness step "${step.label}" is suspiciously tall: ${Math.round(step.height)}px`
    );
  }

  const buttonMetrics = await page.locator(
    ".local-model-installer .privacy-readiness__actions .button, .local-model-installer__button"
  ).evaluateAll((buttons) => buttons.map((button) => {
    const rect = button.getBoundingClientRect();
    return {
      label: button.textContent?.trim() || "button",
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
      clientWidth: button.clientWidth,
      scrollWidth: button.scrollWidth
    };
  }));
  assert.ok(buttonMetrics.length >= 2, `${viewport.name} should render local model action buttons`);
  assertNoFlatRectOverlaps(buttonMetrics, `${viewport.name} local model action buttons`);
  for (const button of buttonMetrics) {
    assert.ok(
      button.scrollWidth <= button.clientWidth + 1,
      `${viewport.name} button "${button.label}" text overflows: scrollWidth=${button.scrollWidth}, clientWidth=${button.clientWidth}`
    );
    assert.ok(
      button.width <= viewport.width,
      `${viewport.name} button "${button.label}" is wider than the viewport: ${Math.round(button.width)}px`
    );
    assert.ok(
      button.width >= 88,
      `${viewport.name} button "${button.label}" is too narrow to review as an action: ${Math.round(button.width)}px`
    );
    assert.ok(
      button.left >= -1 && button.right <= viewport.width + 1,
      `${viewport.name} button "${button.label}" is outside viewport: left=${Math.round(button.left)}px, right=${Math.round(button.right)}px`
    );
    assert.ok(
      button.height <= 76,
      `${viewport.name} button "${button.label}" wraps too tall: ${Math.round(button.height)}px`
    );
  }
}

async function assertLocalModelSetupDetailedLayout(page, viewport) {
  await page.locator(".local-model-installer").first().scrollIntoViewIfNeeded();
  const findings = await page.locator(".local-model-installer").evaluate((installer) => {
    const rectFor = (element) => {
      const rect = element.getBoundingClientRect();
      return {
        top: rect.top,
        bottom: rect.bottom,
        left: rect.left,
        right: rect.right,
        width: rect.width,
        height: rect.height,
        text: (element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80)
      };
    };
    const overlaps = (a, b) =>
      a.left < b.right - 1 &&
      a.right > b.left + 1 &&
      a.top < b.bottom - 1 &&
      a.bottom > b.top + 1;
    const targets = Array.from(installer.querySelectorAll([
      ".privacy-readiness",
      ".local-model-installer__head",
      ".local-model-readiness",
      ".local-model-installer__controls",
      ".local-model-progress"
    ].join(",")), rectFor);
    const result = [];
    for (let index = 0; index < targets.length; index += 1) {
      const target = targets[index];
      if (target.width < 180 || target.height < 20) {
        result.push(`cramped setup block "${target.text}" (${Math.round(target.width)}x${Math.round(target.height)})`);
      }
      for (let next = index + 1; next < targets.length; next += 1) {
        if (overlaps(target, targets[next])) {
          result.push(`overlapping setup blocks "${target.text}" and "${targets[next].text}"`);
        }
      }
    }
    return result;
  });
  assert.deepEqual(findings, [], `${viewport.name} local model setup layout should not overlap or collapse`);
}

function assertNoFlatRectOverlaps(items, label) {
  const visibleItems = items.filter((item) => item.width > layoutTolerancePx && item.height > layoutTolerancePx);
  for (let index = 0; index < visibleItems.length; index += 1) {
    for (let next = index + 1; next < visibleItems.length; next += 1) {
      const first = visibleItems[index];
      const second = visibleItems[next];
      const horizontalOverlap = Math.min(first.right, second.right) - Math.max(first.left, second.left);
      const verticalOverlap = Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top);
      assert.ok(
        horizontalOverlap <= layoutTolerancePx || verticalOverlap <= layoutTolerancePx,
        `${label} should not overlap: "${first.label}" intersects "${second.label}" by ${Math.round(horizontalOverlap)}x${Math.round(verticalOverlap)}px`
      );
    }
  }
}

async function captureEvidence(page, viewport) {
  const boundaryScreenshotPath = screenshotPaths[viewport.name];
  assert.equal(typeof boundaryScreenshotPath, "string", `${viewport.name} boundary screenshot path should be configured`);

  await page.getByLabel("模型边界").scrollIntoViewIfNeeded();
  await delay(100);
  fs.mkdirSync(path.dirname(boundaryScreenshotPath), { recursive: true });
  await page.locator(".model-boundary-profile").screenshot({ path: boundaryScreenshotPath });

  if (viewport.setupScreenshotPath) {
    await page.locator(".local-model-installer").first().scrollIntoViewIfNeeded();
    await delay(100);
    fs.mkdirSync(path.dirname(viewport.setupScreenshotPath), { recursive: true });
    await page.locator(".local-model-installer").first().screenshot({ path: viewport.setupScreenshotPath });
  }
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
    const unavailableBrowserHostAction = async () => ({
      ok: false,
      snapshot: emptyBrowserHostSnapshot,
      error: "Desktop browser host is unavailable in settings local-model smoke"
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
        start: async () => ({ ok: false, error: "not available in settings local-model smoke" }),
        stop: async () => ({ ok: false, error: "not available in settings local-model smoke" }),
        foreground: async () => ({ ok: false, error: "not available in settings local-model smoke" }),
        background: async () => ({ ok: false, error: "not available in settings local-model smoke" })
      },
      localModel: {
        install: async (body) => apiRequest({ endpoint: "/api/settings/install-local-model", method: "POST", body })
      },
      ollama: {
        install: async () => apiRequest({ endpoint: "/api/settings/ollama/install", method: "POST" }),
        start: async () => apiRequest({ endpoint: "/api/settings/ollama/start", method: "POST" }),
        pull: async (body) => apiRequest({ endpoint: "/api/settings/ollama/pull", method: "POST", body })
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
        showItemInFolder: async () => ({ ok: false, error: "not available in settings local-model smoke" })
      },
      notifications: {
        show: async () => ({ shown: false, reason: "not available in settings local-model smoke" }),
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

async function installApiMocks(page, counters, options = {}) {
  const settings = options.settings || backendSettings;
  const localHealth = options.localHealth || localLlmHealth;
  const setupPlan = options.setupPlan || cleanMachineSetupPlan;
  const installLocalModelResponse = options.installLocalModelResponse || null;
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
    if (url.pathname === "/api/chat/proactive-suggestions") return json([]);
    if (url.pathname === "/api/runs") return json([]);
    if (url.pathname === "/api/tasks") return json([]);
    if (url.pathname === "/api/current-plan") return json({});
    if (url.pathname === "/api/approvals/pending") return json([]);
    if (url.pathname === "/api/settings") return json(settings);
    if (url.pathname === "/api/settings/permission-policy") return json({ rules: [], updated_at: "2026-06-08T00:00:00.000Z" });
    if (url.pathname === "/api/pair/devices") return json({ devices: [] });
    if (url.pathname === "/api/settings/local-llm/health") {
      counters.localHealthRequests += 1;
      return json(localHealth);
    }
    if (url.pathname === "/api/settings/local-model/setup-plan") {
      counters.setupPlanRequests += 1;
      return json({
        ...setupPlan,
        model: url.searchParams.get("model") || setupPlan.model || cleanMachineSetupPlan.model
      });
    }
    if (url.pathname === "/api/settings/install-local-model" && method === "POST") {
      counters.installRequests += 1;
      if (installLocalModelResponse) return json(installLocalModelResponse);
      return json({
        ok: true,
        model: cleanMachineSetupPlan.model,
        progress: {
          phase: "switch",
          status: "done",
          model: cleanMachineSetupPlan.model,
          percent: 100
        }
      });
    }
    if (url.pathname === "/api/settings/llm/health") return json({ active: { available: true, provider: "smoke", model: "gpt-4o-mini" }, retry: {} });
    if (url.pathname === "/api/settings/llm/cost-summary") return json({ by_model: [] });
    if (url.pathname === "/api/settings/onnx/status") {
      counters.hardwareRequests += 1;
      return json({
        available: false,
        kind: "onnx",
        model_path: "",
        execution_provider: "",
        available_providers: [],
        generation_runtime: "",
        runtime_packages: {}
      });
    }
    if (url.pathname === "/api/context/usage") return json({});
    if (url.pathname === "/api/audit" || url.pathname === "/api/audit/logs") return json([]);
    if (url.pathname === "/api/browser/sessions") return json({ ok: true, sessions: [] });
    if (url.pathname === "/api/skills") return json({ skills: [], count: 0, directories: [], install_directory: "C:\\Users\\Smoke\\.lengrvis\\skills" });
    if (url.pathname === "/api/system/info") return json({ system: "Windows", platform: "win32", machine: "x64" });
    if (url.pathname === "/api/system/diagnostics") return json(backendDiagnostics);
    if (url.pathname === "/api/system/processes") return json({ processes: [], count: 0 });
    if (url.pathname === "/api/system/startup-items") return json({ startup_items: [], count: 0 });
    if (url.pathname === "/api/apps") return json({ apps: [] });

    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: `unmocked settings local model smoke endpoint: ${method} ${url.pathname}` })
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
