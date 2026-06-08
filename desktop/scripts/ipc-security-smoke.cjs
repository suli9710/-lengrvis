const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");

const originalLoad = Module._load;
const ipcHandlers = new Map();
let dialogOpenResult = { canceled: false, filePaths: ["C:\\Users\\Suli\\Documents\\picked-report.pdf"] };
let messageBoxCalls = [];
let messageBoxResponses = [];
let shellRevealCalls = [];

Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "electron") {
    class Notification {
      static isSupported() {
        return true;
      }

      on() {
        return undefined;
      }

      show() {
        return undefined;
      }
    }

    return {
      app: {
        getPath: (name) => {
          if (name === "userData") return path.resolve(__dirname, "../.tmp/ipc-user-data");
          return path.resolve(__dirname, "../.tmp");
        },
        getFileIcon: async () => ({
          isEmpty: () => false,
          toDataURL: () => "data:image/png;base64,ZmFrZS1pY29u"
        })
      },
      BrowserWindow: {
        fromWebContents: (sender) => sender && sender.__trustedWindow ? sender.__trustedWindow : null,
        getAllWindows: () => []
      },
      BrowserView: class BrowserView {},
      WebContentsView: class WebContentsView {},
      Notification,
      dialog: {
        showOpenDialog: async () => dialogOpenResult,
        showMessageBox: async (...args) => {
          messageBoxCalls.push(args);
          return { response: messageBoxResponses.length ? messageBoxResponses.shift() : 0 };
        }
      },
      ipcMain: { handle: (channel, listener) => ipcHandlers.set(channel, listener) },
      shell: {
        openExternal: async () => undefined,
        showItemInFolder: (filePath) => {
          shellRevealCalls.push(filePath);
        }
      }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const { IPC_CHANNELS } = require("../dist/shared/ipc.js");
const { assertTrustedRenderer, buildRequestUrl, isTrustedRendererUrl, registerIpcHandlers } = require("../dist/main/ipc.js");
const { BrowserHostWebSocketBridge, registerBrowserHostIpcHandlers } = require("../dist/main/browserHost.js");
const { NotificationBridge } = require("../dist/main/notifications.js");

function eventFor(url, trustedWindow = true) {
  const sender = trustedWindow ? { __trustedWindow: {} } : {};
  return {
    sender,
    senderFrame: { url }
  };
}

async function assertRejectsUntrusted(listener, hostCalls) {
  await assert.rejects(
    async () => listener(eventFor("https://evil.example/app"), "session-1"),
    /unknown renderer/
  );
  assert.equal(hostCalls(), 0, "untrusted sender must be rejected before the BrowserHost method is called");
}

(async () => {
  assert.equal(isTrustedRendererUrl("http://127.0.0.1:5173/index.html"), true);
  assert.equal(isTrustedRendererUrl("http://localhost:5173/index.html"), true);
  assert.equal(isTrustedRendererUrl("app://local/index.html"), true);
  assert.equal(isTrustedRendererUrl("app://evil/index.html"), false);
  assert.equal(isTrustedRendererUrl("https://evil.example/index.html"), false);

  const rendererRoot = path.resolve(__dirname, "../dist/renderer/index.html");
  assert.equal(isTrustedRendererUrl(new URL(`file:///${rendererRoot.replace(/\\/g, "/")}`).toString()), true);
  assert.doesNotThrow(() => assertTrustedRenderer(eventFor("http://127.0.0.1:5173/index.html")));
  assert.throws(
    () => assertTrustedRenderer(eventFor("http://127.0.0.1:5173/index.html", false)),
    /untrusted renderer/
  );

  let backendCalls = 0;
  let backendBaseUrl = "http://127.0.0.1:8000";
  const backend = {
    getStatus: () => {
      backendCalls += 1;
      return { state: "running" };
    },
    start: () => {
      backendCalls += 1;
      return { state: "running" };
    },
    stop: () => {
      backendCalls += 1;
      return { state: "stopped" };
    },
    enterForeground: () => {
      backendCalls += 1;
      return { state: "running" };
    },
    enterBackground: () => {
      backendCalls += 1;
      return { state: "running" };
    },
    getBaseUrl: () => backendBaseUrl,
    getDesktopApiToken: () => "desktop-secret"
  };
  registerIpcHandlers(backend);
  const backendStartHandler = ipcHandlers.get(IPC_CHANNELS.backendStart);
  assert.ok(backendStartHandler, "backend start handler must be registered");
  backendCalls = 0;
  await Promise.resolve(backendStartHandler(eventFor("http://127.0.0.1:5173/settings")));
  assert.equal(backendCalls, 1, "trusted renderer should reach backend lifecycle handler");
  backendCalls = 0;
  await assert.rejects(
    async () => backendStartHandler(eventFor("https://evil.example/app")),
    /untrusted renderer/
  );
  assert.equal(backendCalls, 0, "untrusted renderer must not reach backend lifecycle handler");

  const apiRequestHandler = ipcHandlers.get(IPC_CHANNELS.apiRequest);
  assert.ok(apiRequestHandler, "api request handler must be registered");
  assert.equal(
    buildRequestUrl("http://127.0.0.1:8000", { endpoint: "/api/health", query: { probe: "ipc" } }).toString(),
    "http://127.0.0.1:8000/api/health?probe=ipc"
  );
  assert.equal(
    buildRequestUrl("http://localhost:8000", { endpoint: "/api/health" }).toString(),
    "http://localhost:8000/api/health"
  );
  assert.equal(
    buildRequestUrl("http://[::1]:8000", { endpoint: "/api/health" }).toString(),
    "http://[::1]:8000/api/health"
  );
  assert.throws(
    () => buildRequestUrl("http://192.168.1.20:8000", { endpoint: "/api/health" }),
    /loopback backend base URL/
  );
  assert.throws(
    () => buildRequestUrl("https://api.example.test", { endpoint: "/api/health" }),
    /loopback backend base URL/
  );

  const originalFetch = global.fetch;
  let fetchCalls = [];
  global.fetch = async (url, init) => {
    fetchCalls.push({ url: url.toString(), init });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" }
    });
  };

  try {
    const apiResponse = await Promise.resolve(
      apiRequestHandler(eventFor("http://127.0.0.1:5173/api"), {
        endpoint: "/api/health",
        query: { probe: "ipc", ok: true },
        timeoutMs: 1000
      })
    );
    assert.equal(apiResponse.ok, true, "ordinary backend API requests should still pass");
    assert.equal(fetchCalls.length, 1, "valid backend API request should call fetch once");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/health?probe=ipc&ok=true");
    assert.equal(fetchCalls[0].init.method, "GET");
    assert.equal(fetchCalls[0].init.headers["X-Lengrvis-Desktop-Token"], "desktop-secret");

    fetchCalls = [];
    const postResponse = await Promise.resolve(
      apiRequestHandler(eventFor("http://127.0.0.1:5173/api"), {
        endpoint: "/api/chat",
        method: "POST",
        body: { content: "hello" },
        timeoutMs: 1000
      })
    );
    assert.equal(postResponse.ok, true, "ordinary JSON backend API requests should still pass");
    assert.equal(fetchCalls.length, 1, "valid JSON API request should call fetch once");
    assert.equal(fetchCalls[0].init.headers["Content-Type"], "application/json");
    assert.equal(fetchCalls[0].init.body, JSON.stringify({ content: "hello" }));

    fetchCalls = [];
    const runsListResponse = await Promise.resolve(
      apiRequestHandler(eventFor("http://127.0.0.1:5173/api"), {
        endpoint: "/api/runs"
      })
    );
    assert.equal(runsListResponse.ok, true, "read-only run listing should still pass through generic API");
    assert.equal(fetchCalls.length, 1, "read-only run listing should call fetch once");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/runs");
    assert.equal(fetchCalls[0].init.method, "GET");

    fetchCalls = [];
    const settingsReadResponse = await Promise.resolve(
      apiRequestHandler(eventFor("http://127.0.0.1:5173/api"), {
        endpoint: "/api/settings"
      })
    );
    assert.equal(settingsReadResponse.ok, true, "read-only settings fetch should still pass through generic API");
    assert.equal(fetchCalls.length, 1, "read-only settings fetch should call fetch once");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/settings");
    assert.equal(fetchCalls[0].init.method, "GET");

    backendBaseUrl = "https://api.example.test";
    fetchCalls = [];
    const remoteBackendResponse = await Promise.resolve(
      apiRequestHandler(eventFor("http://127.0.0.1:5173/api"), {
        endpoint: "/api/health"
      })
    );
    assert.equal(remoteBackendResponse.ok, false, "non-loopback backend base URL must fail closed before fetch");
    assert.equal(remoteBackendResponse.error.code, "INVALID_RENDERER_API_REQUEST");
    assert.match(remoteBackendResponse.error.message, /loopback backend base URL/);
    assert.equal(fetchCalls.length, 0, "desktop token must not be sent to a non-loopback backend origin");
    backendBaseUrl = "http://127.0.0.1:8000";

    const highRiskBridgeRequests = [
      {
        name: "run start through generic API",
        request: { endpoint: "/api/runs", method: "POST", body: { message: "hello", mode: "efficiency", engine: "auto" } }
      },
      {
        name: "run cancel through generic API",
        request: { endpoint: "/api/runs/run-1/cancel", method: "POST" }
      },
      {
        name: "settings sensitive confirmation through generic API",
        request: { endpoint: "/api/settings/confirm-sensitive-change", method: "POST", body: { allow_browser_network: true } }
      },
      {
        name: "settings save through generic API",
        request: { endpoint: "/api/settings", method: "POST", body: { allow_browser_network: true } }
      },
      {
        name: "permission policy replace through generic API",
        request: { endpoint: "/api/settings/permission-policy", method: "PUT", body: { rules: [] } }
      },
      {
        name: "permission policy relaxation confirmation through generic API",
        request: {
          endpoint: "/api/settings/permission-policy/confirm-relaxation",
          method: "POST",
          body: { action: "delete_rule", rule_id: "weekend_delete" }
        }
      },
      {
        name: "permission rule upsert through generic API",
        request: {
          endpoint: "/api/settings/permission-policy/rules",
          method: "POST",
          body: { id: "weekend_delete", effect: "deny", tools: ["file.trash"], path_patterns: ["*"] }
        }
      },
      {
        name: "permission rule delete through generic API",
        request: { endpoint: "/api/settings/permission-policy/rules/weekend_delete", method: "DELETE" }
      },
      {
        name: "command execute through generic API",
        request: { endpoint: "/api/commands/execute", method: "POST", body: { name: "settings.show", args: {} } }
      },
      {
        name: "cleanup execute through generic API",
        request: { endpoint: "/api/files/cleanup/execute", method: "POST", body: { dry_run: false } }
      },
      {
        name: "cleanup rollback through generic API",
        request: { endpoint: "/api/files/cleanup/rollback", method: "POST", body: { execution_id: "cleanup-1" } }
      },
      {
        name: "skill import through generic API",
        request: { endpoint: "/api/skills/import", method: "POST", body: { path: "C:\\temp\\skill.zip" } }
      },
      {
        name: "skill refresh through generic API",
        request: { endpoint: "/api/skills/refresh", method: "POST" }
      },
      {
        name: "local model install through generic API",
        request: { endpoint: "/api/settings/install-local-model", method: "POST", body: { model: "qwen2.5:3b" } }
      },
      {
        name: "ollama install through generic API",
        request: { endpoint: "/api/settings/ollama/install", method: "POST" }
      },
      {
        name: "ollama pull through generic API",
        request: { endpoint: "/api/settings/ollama/pull", method: "POST", body: { model: "qwen2.5:3b" } }
      },
      {
        name: "ollama start through generic API",
        request: { endpoint: "/api/settings/ollama/start", method: "POST" }
      },
      {
        name: "browser open URL through generic API",
        request: { endpoint: "/api/browser/open-url", method: "POST", body: { url: "https://example.test" } }
      },
      {
        name: "browser session start through generic API",
        request: { endpoint: "/api/browser/session/start", method: "POST", body: { url: "https://example.test" } }
      },
      {
        name: "browser session close through generic API",
        request: { endpoint: "/api/browser/session/close", method: "POST", body: { session_id: "session-1" } }
      },
      {
        name: "browser act through generic API",
        request: { endpoint: "/api/browser/act", method: "POST", body: { action: "click", selector: "button" } }
      },
      {
        name: "browser CUA through generic API",
        request: { endpoint: "/api/browser/cua", method: "POST", body: { instruction: "click the button" } }
      },
      {
        name: "browser CUA run through generic API",
        request: { endpoint: "/api/browser/cua-run", method: "POST", body: { instruction: "click the button" } }
      },
      {
        name: "browser screenshot through generic API",
        request: { endpoint: "/api/browser/screenshot", method: "POST", body: { url: "https://example.test" } }
      },
      {
        name: "document parse through generic API",
        request: { endpoint: "/api/documents/parse", method: "POST", body: { path: "C:\\Users\\Suli\\Documents\\report.pdf" } }
      },
      {
        name: "diagnostics export through generic API",
        request: { endpoint: "/api/system/diagnostics/export", method: "POST" }
      }
    ];

    const blockedRequests = [
      {
        name: "absolute URL",
        request: { endpoint: "https://evil.example/api/health" },
        pattern: /backend-relative endpoints/
      },
      {
        name: "double slash path",
        request: { endpoint: "/api//health" },
        pattern: /backend-relative endpoints|unsafe path separators/
      },
      {
        name: "backslash path",
        request: { endpoint: "/api\\health" },
        pattern: /backend-relative endpoints/
      },
      {
        name: "protocol-relative cross origin",
        request: { endpoint: "//evil.example/api/health" },
        pattern: /backend-relative endpoints/
      },
      {
        name: "dangerous endpoint",
        request: { endpoint: "/api/runtime/foreground", method: "POST" },
        pattern: /explicit desktop bridge/
      },
      {
        name: "mobile pairing request through generic API",
        request: { endpoint: "/api/pair/request", method: "POST" },
        pattern: /explicit desktop bridge/
      },
      {
        name: "mobile pairing device through generic API",
        request: { endpoint: "/api/pair/devices/phone-1", method: "DELETE" },
        pattern: /explicit desktop bridge/
      },
      ...highRiskBridgeRequests.map((testCase) => ({
        ...testCase,
        pattern: /explicit desktop bridge/
      })),
      {
        name: "custom headers",
        request: { endpoint: "/api/health", headers: { Authorization: "Bearer renderer-token" } },
        pattern: /custom headers are not allowed/
      },
      {
        name: "overlarge body",
        request: { endpoint: "/api/chat", method: "POST", body: { content: "x".repeat(600 * 1024) } },
        pattern: /body .*too large/
      }
    ];

    for (const testCase of blockedRequests) {
      fetchCalls = [];
      const blocked = await Promise.resolve(
        apiRequestHandler(eventFor("http://127.0.0.1:5173/api"), testCase.request)
      );
      assert.equal(blocked.ok, false, `${testCase.name} should be rejected`);
      assert.equal(blocked.status, 0, `${testCase.name} should not return a backend status`);
      assert.equal(blocked.error && blocked.error.code, "INVALID_RENDERER_API_REQUEST", `${testCase.name} should fail validation`);
      assert.match(blocked.error && blocked.error.message, testCase.pattern, `${testCase.name} should explain the rejection`);
      assert.equal(fetchCalls.length, 0, `${testCase.name} must be rejected before fetch`);
    }

    const samplePermissionRule = {
      id: "weekend_delete",
      name: "Weekend delete block",
      effect: "deny",
      tools: ["file.trash"],
      path_patterns: ["*"],
      time_windows: [{ days: ["weekend"], start: "00:00", end: "23:59", timezone: "Asia/Shanghai" }],
      enabled: true,
      reason: "Block risky delete windows"
    };

    const explicitBridgeRequests = [
      {
        name: "run start",
        channel: IPC_CHANNELS.runsStart,
        args: [{ message: "hello", mode: "efficiency", engine: "auto" }],
        expectedUrl: "http://127.0.0.1:8000/api/runs",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ message: "hello", mode: "efficiency", engine: "auto" })
      },
      {
        name: "diagnostics export",
        channel: IPC_CHANNELS.systemDiagnosticsExport,
        args: [],
        expectedUrl: "http://127.0.0.1:8000/api/system/diagnostics/export",
        expectedMethod: "POST",
        expectedBody: undefined
      },
      {
        name: "settings sensitive confirmation",
        channel: IPC_CHANNELS.settingsConfirmSensitiveChange,
        args: [{ allow_browser_network: true }],
        expectedUrl: "http://127.0.0.1:8000/api/settings/confirm-sensitive-change",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ allow_browser_network: true })
      },
      {
        name: "settings save",
        channel: IPC_CHANNELS.settingsSave,
        args: [{ allow_browser_network: true, confirmation_nonce: "confirm-1" }],
        expectedUrl: "http://127.0.0.1:8000/api/settings",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ allow_browser_network: true, confirmation_nonce: "confirm-1" })
      },
      {
        name: "permission policy relaxation confirmation",
        channel: IPC_CHANNELS.permissionPolicyConfirmRelaxation,
        args: [{ action: "upsert_rule", rule: samplePermissionRule }],
        expectedUrl: "http://127.0.0.1:8000/api/settings/permission-policy/confirm-relaxation",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ action: "upsert_rule", rule: samplePermissionRule })
      },
      {
        name: "permission rule upsert",
        channel: IPC_CHANNELS.permissionPolicyUpsertRule,
        args: [{ rule: samplePermissionRule, confirmationNonce: "confirm-1" }],
        expectedUrl: "http://127.0.0.1:8000/api/settings/permission-policy/rules?confirmation_nonce=confirm-1",
        expectedMethod: "POST",
        expectedBody: JSON.stringify(samplePermissionRule)
      },
      {
        name: "permission rule delete",
        channel: IPC_CHANNELS.permissionPolicyDeleteRule,
        args: [{ ruleId: "weekend_delete", confirmationNonce: "confirm-1" }],
        expectedUrl: "http://127.0.0.1:8000/api/settings/permission-policy/rules/weekend_delete?confirmation_nonce=confirm-1",
        expectedMethod: "DELETE",
        expectedBody: undefined
      },
      {
        name: "command execute",
        channel: IPC_CHANNELS.commandsExecute,
        args: [{ name: "settings.show", args: { pane: "privacy" } }],
        expectedUrl: "http://127.0.0.1:8000/api/commands/execute",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ name: "settings.show", args: { pane: "privacy" } })
      },
      {
        name: "cleanup execute",
        channel: IPC_CHANNELS.cleanupExecute,
        args: [{ dry_run: false, approval_id: "approval-1" }],
        expectedUrl: "http://127.0.0.1:8000/api/files/cleanup/execute",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ dry_run: false, approval_id: "approval-1" })
      },
      {
        name: "cleanup rollback",
        channel: IPC_CHANNELS.cleanupRollback,
        args: [{ execution_id: "cleanup-1" }],
        expectedUrl: "http://127.0.0.1:8000/api/files/cleanup/rollback",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ execution_id: "cleanup-1" })
      },
      {
        name: "skill import",
        channel: IPC_CHANNELS.skillsImport,
        args: ["C:\\temp\\skill.zip"],
        expectedUrl: "http://127.0.0.1:8000/api/skills/import",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ path: "C:\\temp\\skill.zip" })
      },
      {
        name: "skill refresh",
        channel: IPC_CHANNELS.skillsRefresh,
        args: [],
        expectedUrl: "http://127.0.0.1:8000/api/skills/refresh",
        expectedMethod: "POST",
        expectedBody: undefined
      },
      {
        name: "local model install",
        channel: IPC_CHANNELS.localModelInstall,
        args: [{ model: "qwen2.5:3b" }],
        expectedUrl: "http://127.0.0.1:8000/api/settings/install-local-model",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ model: "qwen2.5:3b" })
      },
      {
        name: "Ollama install",
        channel: IPC_CHANNELS.ollamaInstall,
        args: [],
        expectedUrl: "http://127.0.0.1:8000/api/settings/ollama/install",
        expectedMethod: "POST",
        expectedBody: undefined
      },
      {
        name: "Ollama pull",
        channel: IPC_CHANNELS.ollamaPull,
        args: [{ model: "qwen2.5:3b" }],
        expectedUrl: "http://127.0.0.1:8000/api/settings/ollama/pull",
        expectedMethod: "POST",
        expectedBody: JSON.stringify({ model: "qwen2.5:3b" })
      },
      {
        name: "Ollama start",
        channel: IPC_CHANNELS.ollamaStart,
        args: [],
        expectedUrl: "http://127.0.0.1:8000/api/settings/ollama/start",
        expectedMethod: "POST",
        expectedBody: undefined
      }
    ];

    for (const testCase of explicitBridgeRequests) {
      const handler = ipcHandlers.get(testCase.channel);
      assert.ok(handler, `${testCase.name} explicit bridge handler must be registered`);
      fetchCalls = [];
      const response = await Promise.resolve(handler(eventFor("http://127.0.0.1:5173/settings"), ...testCase.args));
      assert.equal(response.ok, true, `${testCase.name} explicit bridge should call backend`);
      assert.equal(fetchCalls.length, 1, `${testCase.name} explicit bridge should use fetch once`);
      assert.equal(fetchCalls[0].url, testCase.expectedUrl);
      assert.equal(fetchCalls[0].init.method, testCase.expectedMethod);
      assert.equal(fetchCalls[0].init.body, testCase.expectedBody);
      await assert.rejects(
        async () => handler(eventFor("https://evil.example/app"), ...testCase.args),
        /untrusted renderer/,
        `${testCase.name} explicit bridge should reject untrusted renderers`
      );
    }

    const chooseDocumentHandler = ipcHandlers.get(IPC_CHANNELS.chooseDocument);
    const documentParseHandler = ipcHandlers.get(IPC_CHANNELS.documentsParse);
    const documentAskHandler = ipcHandlers.get(IPC_CHANNELS.documentsAsk);
    const documentCompareHandler = ipcHandlers.get(IPC_CHANNELS.documentsCompare);
    assert.ok(chooseDocumentHandler, "choose document handler must be registered");
    assert.ok(documentParseHandler, "document parse bridge handler must be registered");
    assert.ok(documentAskHandler, "document ask bridge handler must be registered");
    assert.ok(documentCompareHandler, "document compare bridge handler must be registered");

    const pickedDocument = "C:\\Users\\Suli\\Documents\\picked-report.pdf";
    dialogOpenResult = { canceled: false, filePaths: [pickedDocument] };
    messageBoxCalls = [];
    fetchCalls = [];
    const pickedPath = await Promise.resolve(chooseDocumentHandler(eventFor("http://127.0.0.1:5173/files")));
    assert.equal(pickedPath, pickedDocument, "choose document should return the selected document path");
    const pickedParseResponse = await Promise.resolve(
      documentParseHandler(eventFor("http://127.0.0.1:5173/files"), { path: pickedDocument, includeText: true })
    );
    assert.equal(pickedParseResponse.ok, true, "picked document should be allowed without another native prompt");
    assert.equal(messageBoxCalls.length, 0, "previously picked documents should be granted in-process");
    assert.equal(fetchCalls.length, 1, "picked document parse should call backend once");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/documents/parse");
    assert.equal(fetchCalls[0].init.method, "POST");
    assert.equal(fetchCalls[0].init.body, JSON.stringify({ path: pickedDocument, include_text: true }));

    const pastedDocument = "C:\\Users\\Suli\\Documents\\pasted-report.pdf";
    messageBoxCalls = [];
    messageBoxResponses = [0];
    fetchCalls = [];
    const pastedAskResponse = await Promise.resolve(
      documentAskHandler(eventFor("http://127.0.0.1:5173/files"), {
        path: pastedDocument,
        question: "Summarize this document",
        topK: 3
      })
    );
    assert.equal(pastedAskResponse.ok, true, "pasted document should proceed after native confirmation");
    assert.equal(messageBoxCalls.length, 1, "pasted document should require native confirmation");
    assert.equal(fetchCalls.length, 1, "confirmed pasted document should call backend once");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/documents/ask");
    assert.equal(fetchCalls[0].init.body, JSON.stringify({ path: pastedDocument, question: "Summarize this document", top_k: 3 }));

    messageBoxCalls = [];
    fetchCalls = [];
    await assert.rejects(
      async () =>
        documentAskHandler(eventFor("http://127.0.0.1:5173/files"), {
          documentId: "doc-123",
          question: "What changed?",
          topK: 2
        }),
      /document ask request field is not allowed/,
      "document id only ask should be rejected until the backend supports it"
    );
    assert.equal(messageBoxCalls.length, 0, "invalid document id ask should not ask for a path grant");
    assert.equal(fetchCalls.length, 0, "invalid document id ask must not call backend");

    messageBoxCalls = [];
    messageBoxResponses = [1];
    fetchCalls = [];
    await assert.rejects(
      async () =>
        documentCompareHandler(eventFor("http://127.0.0.1:5173/files"), {
          paths: ["C:\\Users\\Suli\\Documents\\left.pdf", "C:\\Users\\Suli\\Documents\\right.pdf"]
        }),
      /not confirmed/,
      "document bridge should reject when native confirmation is denied"
    );
    assert.equal(fetchCalls.length, 0, "denied document confirmation must not call backend");
    assert.equal(messageBoxCalls.length, 1, "denied document comparison should ask exactly once");

    fetchCalls = [];
    await assert.rejects(
      async () =>
        documentParseHandler(eventFor("http://127.0.0.1:5173/files"), {
          path: pickedDocument,
          headers: { Authorization: "Bearer renderer-token" }
        }),
      /document parse request field is not allowed/,
      "document bridge should reject unexpected fields"
    );
    assert.equal(fetchCalls.length, 0, "invalid document bridge request must be rejected before fetch");

    const settingsSaveHandler = ipcHandlers.get(IPC_CHANNELS.settingsSave);
    const settingsConfirmHandler = ipcHandlers.get(IPC_CHANNELS.settingsConfirmSensitiveChange);
    assert.ok(settingsConfirmHandler, "settings confirmation explicit bridge handler must be registered");
    assert.ok(settingsSaveHandler, "settings save explicit bridge handler must be registered");
    fetchCalls = [];
    await assert.rejects(
      async () =>
        settingsSaveHandler(eventFor("http://127.0.0.1:5173/settings"), {
          headers: { Authorization: "Bearer renderer-token" }
        }),
      /settings patch field is not allowed/,
      "settings save bridge should reject unexpected body fields"
    );
    assert.equal(fetchCalls.length, 0, "invalid settings save bridge request must be rejected before fetch");

    messageBoxCalls = [];
    fetchCalls = [];
    const ordinarySettingsResponse = await Promise.resolve(
      settingsSaveHandler(eventFor("http://127.0.0.1:5173/settings"), {
        mode: "efficiency",
        provider_name: "openai_compatible",
        model: "gpt-4o-mini",
        temperature: 0.2
      })
    );
    assert.equal(ordinarySettingsResponse.ok, true, "ordinary settings save should not require sensitive confirmation");
    assert.equal(messageBoxCalls.length, 0, "ordinary settings save must not show native sensitive confirmation");
    assert.equal(fetchCalls.length, 1, "ordinary settings save should call backend once");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/settings");
    assert.equal(fetchCalls[0].init.method, "POST");
    assert.equal(
      fetchCalls[0].init.body,
      JSON.stringify({
        mode: "efficiency",
        provider_name: "openai_compatible",
        model: "gpt-4o-mini",
        temperature: 0.2
      })
    );

    messageBoxCalls = [];
    fetchCalls = [];
    const ordinaryConfirmationResponse = await Promise.resolve(
      settingsConfirmHandler(eventFor("http://127.0.0.1:5173/settings"), {
        mode: "efficiency",
        provider_name: "openai_compatible",
        model: "gpt-4o-mini"
      })
    );
    assert.equal(ordinaryConfirmationResponse.ok, true, "ordinary settings confirmation preparation should still proxy");
    assert.equal(messageBoxCalls.length, 0, "ordinary settings confirmation preparation must not show native sensitive confirmation");
    assert.equal(fetchCalls.length, 1, "ordinary settings confirmation preparation should call backend once");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/settings/confirm-sensitive-change");
    assert.equal(fetchCalls[0].init.method, "POST");

    fetchCalls = [];
    await assert.rejects(
      async () =>
        settingsSaveHandler(eventFor("http://127.0.0.1:5173/settings"), {
          base_url: "https://attacker.example/v1"
        }),
      /prior confirmation/,
      "settings save bridge should reject LLM endpoint changes without a bound confirmation"
    );
    assert.equal(fetchCalls.length, 0, "unconfirmed LLM endpoint settings must be rejected before fetch");

    messageBoxCalls = [];
    messageBoxResponses = [1];
    fetchCalls = [];
    await assert.rejects(
      async () => settingsConfirmHandler(eventFor("http://127.0.0.1:5173/settings"), { remote_desktop_enabled: true }),
      /not confirmed/,
      "settings confirmation bridge should require native confirmation"
    );
    assert.equal(fetchCalls.length, 0, "denied native settings confirmation must not call backend");
    assert.equal(messageBoxCalls.length, 1, "settings confirmation should ask exactly once");

    const permissionUpsertHandler = ipcHandlers.get(IPC_CHANNELS.permissionPolicyUpsertRule);
    assert.ok(permissionUpsertHandler, "permission rule explicit bridge handler must be registered");
    fetchCalls = [];
    await assert.rejects(
      async () =>
        permissionUpsertHandler(eventFor("http://127.0.0.1:5173/settings"), {
          rule: { ...samplePermissionRule, headers: { Authorization: "Bearer renderer-token" } }
        }),
      /permission rule field is not allowed/,
      "permission rule bridge should reject unexpected rule fields"
    );
    assert.equal(fetchCalls.length, 0, "invalid permission rule bridge request must be rejected before fetch");

    const mobilePairingCreateCodeHandler = ipcHandlers.get(IPC_CHANNELS.mobilePairingCreateCode);
    assert.ok(mobilePairingCreateCodeHandler, "mobile pairing create-code handler must be registered");
    fetchCalls = [];
    const pairingResponse = await Promise.resolve(mobilePairingCreateCodeHandler(eventFor("http://127.0.0.1:5173/settings")));
    assert.equal(pairingResponse.ok, true, "explicit mobile pairing bridge should call backend");
    assert.equal(fetchCalls.length, 1, "explicit mobile pairing bridge should use fetch once");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/pair/request");
    assert.equal(fetchCalls[0].init.method, "POST");

    await assert.rejects(
      async () => mobilePairingCreateCodeHandler(eventFor("https://evil.example/app")),
      /untrusted renderer/
    );

    const mobilePairingGrantHandler = ipcHandlers.get(IPC_CHANNELS.mobilePairingCreateRemoteInputGrant);
    assert.ok(mobilePairingGrantHandler, "mobile pairing remote-input grant handler must be registered");
    fetchCalls = [];
    const grantResponse = await Promise.resolve(
      mobilePairingGrantHandler(eventFor("http://127.0.0.1:5173/settings"), {
        deviceId: "phone-1",
        expiresInSeconds: 300
      })
    );
    assert.equal(grantResponse.ok, true, "explicit remote-input grant bridge should call backend");
    assert.equal(fetchCalls[0].url, "http://127.0.0.1:8000/api/pair/devices/phone-1/remote-input-grants");
    assert.equal(fetchCalls[0].init.method, "POST");
    assert.equal(fetchCalls[0].init.body, JSON.stringify({ expires_in: 300 }));

    messageBoxCalls = [];
    messageBoxResponses = [1];
    fetchCalls = [];
    await assert.rejects(
      async () =>
        mobilePairingGrantHandler(eventFor("http://127.0.0.1:5173/settings"), {
          deviceId: "phone-2",
          expiresInSeconds: 300
        }),
      /not confirmed/,
      "remote-input grant bridge should require native confirmation"
    );
    assert.equal(fetchCalls.length, 0, "denied native remote-input confirmation must not call backend");
    assert.equal(messageBoxCalls.length, 1, "remote-input grant should ask exactly once");
  } finally {
    global.fetch = originalFetch;
  }

  const getFileIconHandler = ipcHandlers.get(IPC_CHANNELS.getFileIcon);
  const showItemInFolderHandler = ipcHandlers.get(IPC_CHANNELS.showItemInFolder);
  assert.ok(getFileIconHandler, "file icon handler must be registered");
  assert.ok(showItemInFolderHandler, "show item in folder handler must be registered");
  const icon = await Promise.resolve(getFileIconHandler(eventFor("http://127.0.0.1:5173/apps"), __filename));
  assert.equal(icon, "data:image/png;base64,ZmFrZS1pY29u");
  await assert.rejects(
    async () => getFileIconHandler(eventFor("https://evil.example/app"), __filename),
    /untrusted renderer/
  );
  shellRevealCalls = [];
  const deniedReveal = await Promise.resolve(showItemInFolderHandler(eventFor("http://127.0.0.1:5173/apps"), "C:\\Windows\\win.ini"));
  assert.equal(deniedReveal.ok, false, "ungranted paths must not be revealed");
  assert.equal(deniedReveal.path, "", "ungranted reveal requests must not echo resolved local paths");
  assert.equal(shellRevealCalls.length, 0, "ungranted reveal requests must not call shell.showItemInFolder");
  dialogOpenResult = { canceled: false, filePaths: [__filename] };
  const grantedRevealPath = await Promise.resolve(ipcHandlers.get(IPC_CHANNELS.chooseDocument)(eventFor("http://127.0.0.1:5173/files")));
  const revealResponse = await Promise.resolve(showItemInFolderHandler(eventFor("http://127.0.0.1:5173/apps"), grantedRevealPath));
  assert.equal(revealResponse.ok, true, "previously selected document path should be revealable");
  assert.equal(shellRevealCalls.length, 1, "granted reveal should call shell.showItemInFolder once");
  assert.equal(shellRevealCalls[0], path.resolve(__filename));

  const notificationBridge = new NotificationBridge({ backend, getMainWindow: () => null });
  notificationBridge.registerIpcHandlers();
  const notificationHandler = ipcHandlers.get(IPC_CHANNELS.showNotification);
  assert.ok(notificationHandler, "notification handler must be registered");
  await assert.rejects(
    async () => notificationHandler(eventFor("https://evil.example/app"), { title: "nope", body: "blocked", severity: "info" }),
    /untrusted renderer/
  );

  const handlers = new Map();
  let calls = 0;
  let takeoverCalls = 0;
  let performActionCalls = 0;
  let performedActions = [];
  const host = {
    getSnapshot: () => {
      calls += 1;
      return { sessions: [], events: [], visible: false, hostAvailable: true };
    },
    onSnapshot: () => () => undefined,
    open: async () => {
      calls += 1;
      return {
        ok: false,
        error: "Navigation failed for https://example.test/#/callback?access_token=secret-token&client_secret=secret-token"
      };
    },
    show: () => {
      calls += 1;
      return { ok: true };
    },
    hide: () => {
      calls += 1;
      return { ok: true };
    },
    setBounds: () => {
      calls += 1;
      return { ok: true };
    },
    pause: () => {
      calls += 1;
      return { ok: true };
    },
    resume: () => {
      calls += 1;
      return { ok: true };
    },
    takeover: () => {
      calls += 1;
      takeoverCalls += 1;
      return { ok: true };
    },
    release: () => {
      calls += 1;
      return { ok: true };
    },
    stop: async () => {
      calls += 1;
      return { ok: true };
    },
    performAction: async (sessionId, action) => {
      calls += 1;
      performActionCalls += 1;
      performedActions.push({ sessionId, kind: action?.kind });
      return { ok: true };
    }
  };

  registerBrowserHostIpcHandlers({
    handle: (channel, listener) => handlers.set(channel, listener),
    host
  });

  const snapshotHandler = handlers.get(IPC_CHANNELS.browserHostSnapshot);
  assert.ok(snapshotHandler, "browser host snapshot handler must be registered");

  calls = 0;
  const snapshot = await Promise.resolve(snapshotHandler(eventFor("http://127.0.0.1:5173/browser")));
  assert.equal(snapshot.hostAvailable, true);
  assert.equal(calls, 1, "trusted renderer should reach the BrowserHost handler");

  calls = 0;
  await assertRejectsUntrusted(snapshotHandler, () => calls);

  const pauseHandler = handlers.get(IPC_CHANNELS.browserHostPause);
  assert.ok(pauseHandler, "browser host pause handler must be registered");
  calls = 0;
  await assertRejectsUntrusted(pauseHandler, () => calls);

  calls = 0;
  await assert.rejects(
    async () => snapshotHandler(eventFor("http://127.0.0.1:5173/browser", false)),
    /unknown renderer/
  );
  assert.equal(calls, 0, "sender without owning BrowserWindow must be rejected before handler execution");

  const openHandler = handlers.get(IPC_CHANNELS.browserHostOpen);
  assert.ok(openHandler, "browser host open handler must be registered");
  calls = 0;
  const openResult = await Promise.resolve(openHandler(eventFor("http://127.0.0.1:5173/browser"), { url: "https://example.test/?token=secret" }));
  assert.equal(calls, 1, "trusted open request should reach BrowserHost");
  assert.equal(
    openResult.error,
    "Navigation failed for https://example.test/#/callback?access_token=[redacted]&client_secret=[redacted]"
  );
  assert.equal(JSON.stringify(openResult).includes("secret-token"), false, "browser host action results must redact tokens in error URLs");

  const takeoverHandler = handlers.get(IPC_CHANNELS.browserHostTakeover);
  const actionHandler = handlers.get(IPC_CHANNELS.browserHostAction);
  assert.ok(takeoverHandler, "browser host takeover handler must be registered");
  assert.ok(actionHandler, "browser host action handler must be registered");

  takeoverCalls = 0;
  const deniedTakeover = await Promise.resolve(takeoverHandler(eventFor("http://127.0.0.1:5173/browser"), "session-1"));
  assert.equal(deniedTakeover.ok, false, "renderer takeover should be denied without approval grant");
  assert.match(deniedTakeover.error, /approval grant/);
  assert.equal(takeoverCalls, 0, "renderer takeover denial must not call the host takeover method");

  const deniedRendererInputActions = [
    { name: "click", action: { kind: "click", selector: "#submit", approved: true, approval_id: "forged-approval" } },
    { name: "fill", action: { kind: "fill", selector: "#email", text: "secret", approved: true, approval_id: "forged-approval" } },
    { name: "submit", action: { kind: "submit", selector: "form", approved: true, approval_id: "forged-approval" } },
    { name: "scroll", action: { kind: "scroll", fields: { y: "600" }, approved: true, approval_id: "forged-approval" } },
    { name: "cua", action: { kind: "cua", text: "click the button", approved: true, approval_id: "forged-approval" } }
  ];
  for (const testCase of deniedRendererInputActions) {
    performActionCalls = 0;
    performedActions = [];
    const denied = await Promise.resolve(
      actionHandler(eventFor("http://127.0.0.1:5173/browser"), {
        sessionId: "session-1",
        action: testCase.action
      })
    );
    assert.equal(denied.ok, false, `renderer ${testCase.name} action should be denied without a desktop approval grant`);
    assert.match(denied.error, /approval grant/);
    assert.equal(performActionCalls, 0, `renderer ${testCase.name} denial must not call the host action method`);
    assert.deepEqual(performedActions, [], `renderer ${testCase.name} denial must not record host actions`);
  }

  performActionCalls = 0;
  performedActions = [];
  for (const testCase of [
    { name: "observe", action: { kind: "observe" } },
    { name: "screenshot", action: { kind: "screenshot" } }
  ]) {
    const result = await Promise.resolve(
      actionHandler(eventFor("http://127.0.0.1:5173/browser"), {
        sessionId: "session-1",
        action: testCase.action
      })
    );
    assert.equal(result.ok, true, `renderer ${testCase.name} action should remain read-only`);
  }
  assert.deepEqual(
    performedActions,
    [
      { sessionId: "session-1", kind: "observe" },
      { sessionId: "session-1", kind: "screenshot" }
    ],
    "renderer read-only BrowserHost actions should call the host exactly once each"
  );
  assert.equal(performActionCalls, 2, "renderer read-only BrowserHost actions should be the only host actions performed");

  const redactedSnapshot = {
    sessions: [
      {
        id: "session-1",
        current_url: "https://user:password@example.test/path?token=secret-token&safe=1#/callback?access_token=secret-token",
        title: "Secret",
        status: "idle",
        mode: "watch",
        created_at: "2026-05-27T00:00:00.000Z",
        updated_at: "2026-05-27T00:00:00.000Z",
        paused: false,
        takeover: false,
        last_observation: {
          url: "https://example.test/observe?access_token=hidden#id_token=hidden",
          links: [{ text: "ok", url: "https://example.test/link?password=hidden" }]
        }
      }
    ],
    events: [
      {
        id: "event-1",
        session_id: "session-1",
        type: "action.fill",
        action: {
          kind: "fill",
          url: "https://example.test/fill?token=secret-token#client_secret=secret-token",
          text: "secret typed text",
          fields: { "#token": "secret field value" }
        },
        url: "https://example.test/event?refresh_token=secret-token#oauth_token=secret-token",
        screenshot_url: "data:image/png;base64,secret",
        error: "Selector not found: #password input[name=\"password\"] [data-token=\"secret-token\"] at https://example.test/#/callback?access_token=secret-token&client_secret=secret-token token=secret-token",
        ok: true,
        created_at: "2026-05-27T00:00:01.000Z"
      }
    ],
    visible: false,
    hostAvailable: true
  };
  host.getSnapshot = () => redactedSnapshot;
  const sanitized = await Promise.resolve(snapshotHandler(eventFor("http://127.0.0.1:5173/browser")));
  const sanitizedText = JSON.stringify(sanitized);
  assert.equal(sanitized.sessions[0].current_url, "https://%5Bredacted%5D:%5Bredacted%5D@example.test/path?token=%5Bredacted%5D&safe=1#/callback?access_token=[redacted]");
  assert.equal(sanitized.events[0].action.text, "[redacted]");
  assert.equal(sanitized.events[0].action.fields.field_1, "[redacted]");
  assert.equal(
    sanitized.events[0].error,
    "Selector not found: #[redacted] [redacted] [redacted] at https://example.test/#/callback?access_token=[redacted]&client_secret=[redacted] token=[redacted]"
  );
  assert.equal(sanitized.events[0].screenshot_url, "[redacted:screenshot]");
  assert.equal(sanitizedText.includes("secret-token"), false, "browser host snapshots must redact sensitive URL tokens");
  assert.equal(sanitizedText.includes("hidden"), false, "browser host observations must redact sensitive URL tokens");
  assert.equal(sanitizedText.includes("secret typed text"), false, "browser host snapshots must redact typed text");
  assert.equal(sanitizedText.includes("secret field value"), false, "browser host snapshots must redact fill fields");
  assert.equal(sanitizedText.includes("#password"), false, "browser host errors must redact sensitive selectors");
  assert.equal(sanitizedText.includes("input[name=\"password\"]"), false, "browser host errors must redact password attribute selectors");
  assert.equal(sanitizedText.includes("[data-token=\"secret-token\"]"), false, "browser host errors must redact token attribute selectors");

  const originalWebSocket = global.WebSocket;
  const sentMessages = [];
  class FakeWebSocket {
    static CONNECTING = 0;
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
    backendBaseUrl = "http://127.0.0.1:8000";
    FakeWebSocket.last = null;
    const loopbackNotificationBridge = new NotificationBridge({ backend, getMainWindow: () => null });
    loopbackNotificationBridge.startBackendListener();
    assert.equal(FakeWebSocket.last.url, "ws://127.0.0.1:8000/ws/notifications");
    assert.deepEqual(FakeWebSocket.last.protocols, ["lengrvis.desktop.token.desktop-secret"]);
    loopbackNotificationBridge.stopBackendListener();

    backendBaseUrl = "https://api.example.test";
    FakeWebSocket.last = null;
    const remoteNotificationBridge = new NotificationBridge({ backend, getMainWindow: () => null });
    remoteNotificationBridge.startBackendListener();
    assert.equal(FakeWebSocket.last, null, "notification bridge must not send desktop token to non-loopback backend WS");
    remoteNotificationBridge.stopBackendListener();
    backendBaseUrl = "http://127.0.0.1:8000";

    takeoverCalls = 0;
    performActionCalls = 0;
    performedActions = [];
    const bridge = new BrowserHostWebSocketBridge(host, () => "http://127.0.0.1:8000", () => "desktop-secret");
    bridge.start();
    assert.equal(new URL(FakeWebSocket.last.url).searchParams.get("desktop_token"), null);
    assert.deepEqual(FakeWebSocket.last.protocols, ["lengrvis.desktop.token.desktop-secret"]);
    FakeWebSocket.last.listeners.open();
    assert.equal(sentMessages[0].type, "snapshot", "BrowserHost WS bridge should send snapshots after protocol auth");

    const remoteWriteMessages = [
      { type: "takeover", request_id: "takeover-1", session_id: "session-1" },
      { type: "action", request_id: "click-1", session_id: "session-1", action: { kind: "click", selector: "#submit" } },
      { type: "action", request_id: "fill-1", session_id: "session-1", action: { kind: "fill", selector: "#email", text: "secret" } },
      { type: "action", request_id: "submit-1", session_id: "session-1", action: { kind: "submit", selector: "form" } }
    ];
    for (const message of remoteWriteMessages) {
      FakeWebSocket.last.listeners.message({ data: JSON.stringify(message) });
    }
    for (const message of [
      { type: "action", request_id: "observe-1", session_id: "session-1", action: { kind: "observe" } },
      { type: "action", request_id: "screenshot-1", session_id: "session-1", action: { kind: "screenshot" } }
    ]) {
      FakeWebSocket.last.listeners.message({ data: JSON.stringify(message) });
    }

    await new Promise((resolve) => setTimeout(resolve, 0));
    const remoteResults = new Map(sentMessages.filter((message) => message.type === "result").map((message) => [message.request_id, message]));
    for (const requestId of ["takeover-1", "click-1", "fill-1", "submit-1"]) {
      assert.equal(remoteResults.get(requestId)?.ok, false, `BrowserHost WS ${requestId} should be denied without a desktop grant`);
      assert.match(remoteResults.get(requestId)?.error ?? "", /approval grant/);
    }
    assert.equal(remoteResults.get("observe-1")?.ok, true, "BrowserHost WS observe should remain read-only");
    assert.equal(remoteResults.get("screenshot-1")?.ok, true, "BrowserHost WS screenshot should remain read-only");
    assert.equal(takeoverCalls, 0, "BrowserHost WS takeover denial must not call the host takeover method");
    assert.deepEqual(
      performedActions,
      [
        { sessionId: "session-1", kind: "observe" },
        { sessionId: "session-1", kind: "screenshot" }
      ],
      "BrowserHost WS bridge should only perform read-only remote actions without a desktop grant"
    );
    assert.equal(performActionCalls, 2, "BrowserHost WS bridge must not perform denied remote input actions");
    bridge.stop();
  } finally {
    global.WebSocket = originalWebSocket;
  }

  console.log("IPC security smoke passed");
})();
