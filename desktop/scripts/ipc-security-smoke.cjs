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
const { assertTrustedRenderer, isTrustedRendererUrl, registerIpcHandlers } = require("../dist/main/ipc.js");
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
