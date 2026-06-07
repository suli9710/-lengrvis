const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");

const originalLoad = Module._load;
const ipcHandlers = new Map();

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
      dialog: {},
      ipcMain: { handle: (channel, listener) => ipcHandlers.set(channel, listener) },
      shell: { openExternal: async () => undefined }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const { IPC_CHANNELS } = require("../dist/shared/ipc.js");
const { assertTrustedRenderer, buildRequestUrl, isTrustedRendererUrl, registerIpcHandlers } = require("../dist/main/ipc.js");
const { registerBrowserHostIpcHandlers } = require("../dist/main/browserHost.js");
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
    getBaseUrl: () => "http://127.0.0.1:8000",
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

    const highRiskBridgeRequests = [
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

    const explicitBridgeRequests = [
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
  } finally {
    global.fetch = originalFetch;
  }

  const getFileIconHandler = ipcHandlers.get(IPC_CHANNELS.getFileIcon);
  assert.ok(getFileIconHandler, "file icon handler must be registered");
  const icon = await Promise.resolve(getFileIconHandler(eventFor("http://127.0.0.1:5173/apps"), __filename));
  assert.equal(icon, "data:image/png;base64,ZmFrZS1pY29u");
  await assert.rejects(
    async () => getFileIconHandler(eventFor("https://evil.example/app"), __filename),
    /untrusted renderer/
  );

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
  const host = {
    getSnapshot: () => {
      calls += 1;
      return { sessions: [], events: [], visible: false, hostAvailable: true };
    },
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
    performAction: async () => {
      calls += 1;
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

  console.log("IPC security smoke passed");
})();
