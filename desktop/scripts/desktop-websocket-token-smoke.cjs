const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");

const originalLoad = Module._load;

Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "electron") {
    return {
      app: {
        getFileIcon: async () => null
      },
      BrowserWindow: {
        fromWebContents: () => ({}),
        getAllWindows: () => []
      },
      dialog: {},
      ipcMain: { handle: () => undefined },
      shell: { openExternal: async () => undefined }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const {
  buildBackendWebSocketUrl,
  desktopWebSocketProtocols
} = require("../dist/main/desktopWebSocket.js");

assert.deepEqual(
  desktopWebSocketProtocols(" desktop-secret "),
  ["mavris.desktop.token.desktop-secret"],
  "desktop WebSocket protocol must carry the desktop token"
);

try {
  desktopWebSocketProtocols("");
  assert.fail("empty desktop token must fail closed");
} catch (error) {
  assert.match(error.message, /token is required/);
}

assert.equal(
  buildBackendWebSocketUrl("http://127.0.0.1:8000", "/ws/tasks/task-1", { cursor: 1, ignored: null }),
  "ws://127.0.0.1:8000/ws/tasks/task-1?cursor=1"
);
assert.equal(
  buildBackendWebSocketUrl("https://localhost:8443", "/api/ws/settings/install-local-model", { model: "qwen2.5:3b" }),
  "wss://localhost:8443/api/ws/settings/install-local-model?model=qwen2.5%3A3b"
);

for (const endpoint of ["ws://127.0.0.1:8000/ws/tasks/1", "//evil.test/ws", "api/ws", "/api\\ws"]) {
  assert.throws(
    () => buildBackendWebSocketUrl("http://127.0.0.1:8000", endpoint),
    /backend-relative/,
    `endpoint ${endpoint} must be rejected`
  );
}

const apiClientSource = fs.readFileSync(path.join(__dirname, "..", "src", "renderer", "lib", "apiClient.ts"), "utf8");
assert.match(
  apiClientSource,
  /function subscribeDesktopJsonStream[\s\S]*window\.mavris(?:!|\?)?\.realtime\.subscribe/,
  "task/run streams should use preload realtime bridge in Electron"
);
assert.doesNotMatch(apiClientSource, /new WebSocket\(build(?:Task|Run)WebSocketUrl/, "task/run streams must not directly create protected WebSockets");
assert.match(apiClientSource, /function isWebOnlyDevRealtimeFallbackEnabled[\s\S]*import\.meta\.env\.DEV/, "renderer fallback must be web-only dev gated");

const settingsSource = fs.readFileSync(path.join(__dirname, "..", "src", "renderer", "components", "SettingsPanel.tsx"), "utf8");
assert.match(settingsSource, /subscribeInstallModelProgressSocket/, "install progress should use the shared subscribe helper");
assert.doesNotMatch(settingsSource, /new WebSocket\(buildInstallModelWebSocketUrl/, "install progress must not directly create protected WebSockets");
assert.match(settingsSource, /function isInstallModelWebOnlyDevFallbackEnabled[\s\S]*import\.meta\.env\.DEV/, "install progress fallback must be web-only dev gated");

const preloadSource = fs.readFileSync(path.join(__dirname, "..", "src", "preload", "preload.ts"), "utf8");
assert.doesNotMatch(preloadSource, /MAVRIS_DESKTOP_API_TOKEN/, "preload must not expose or read the desktop token");

console.log("desktop WebSocket token smoke passed");
