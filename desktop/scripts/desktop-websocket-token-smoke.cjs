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
  ["lengrvis.desktop.token.desktop-secret"],
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
assert.throws(
  () => buildBackendWebSocketUrl("https://localhost:8443", "/api/ws/settings/install-local-model", { model: "qwen2.5:3b" }),
  /passive subscription allowlist/,
  "model installation must not use the generic desktop WebSocket bridge"
);

for (const baseUrl of ["http://192.168.1.20:8000", "https://api.example.test"]) {
  assert.throws(
    () => buildBackendWebSocketUrl(baseUrl, "/ws/tasks/task-1"),
    /loopback backend base URL/,
    `desktop token-bearing WebSocket must reject non-loopback backend ${baseUrl}`
  );
}

for (const endpoint of ["ws://127.0.0.1:8000/ws/tasks/1", "//evil.test/ws", "api/ws", "/api\\ws"]) {
  assert.throws(
    () => buildBackendWebSocketUrl("http://127.0.0.1:8000", endpoint),
    /backend-relative/,
    `endpoint ${endpoint} must be rejected`
  );
}

for (const endpoint of ["/ws/tasks/task-1?desktop_token=leak", "/ws/tasks/task-1#fragment"]) {
  assert.throws(
    () => buildBackendWebSocketUrl("http://127.0.0.1:8000", endpoint),
    /query strings or fragments/,
    `endpoint ${endpoint} must not smuggle URL state`
  );
}

for (const endpoint of ["/ws//tasks/task-1", "/ws/%2e%2e/api/ws/browser-host", "/ws/%2Ftasks/task-1"]) {
  assert.throws(
    () => buildBackendWebSocketUrl("http://127.0.0.1:8000", endpoint),
    /backend-relative|unsafe path|encoded path|unsafe path segments/,
    `endpoint ${endpoint} must not bypass backend WebSocket path validation`
  );
}

for (const [name, query] of [
  ["array query", ["cursor"]],
  ["nested query value", { cursor: { next: 1 } }],
  ["unsafe query key", { "bad\nkey": "cursor" }],
  ["unsafe reserved key", { constructor: "polluted" }],
  ["non-finite query number", { cursor: Number.POSITIVE_INFINITY }]
]) {
  assert.throws(
    () => buildBackendWebSocketUrl("http://127.0.0.1:8000", "/ws/tasks/task-1", query),
    /query/,
    `${name} must be rejected before opening a token-bearing desktop WebSocket`
  );
}

const transportSource = fs.readFileSync(path.join(__dirname, "..", "src", "renderer", "lib", "api", "transport.ts"), "utf8");
assert.match(
  transportSource,
  /function subscribeDesktopJsonStream[\s\S]*window\.lengrvis(?:!|\?)?\.realtime\.subscribe/,
  "task/run streams should use preload realtime bridge in Electron"
);
assert.doesNotMatch(transportSource, /new WebSocket\(build(?:Task|Run)WebSocketUrl/, "task/run streams must not directly create protected WebSockets");
assert.match(
  transportSource,
  /function isWebOnlyDev(?:RealtimeFallbackEnabled|BackendBridge)[\s\S]*import\.meta\.env\.DEV/,
  "renderer fallback must be web-only dev gated"
);

assert.match(
  transportSource,
  /dev:web only: VITE_LENGRVIS_DESKTOP_API_TOKEN/,
  "dev:web desktop token bypass must document the security risk"
);

const viteConfig = fs.readFileSync(path.join(__dirname, "..", "vite.config.ts"), "utf8");
assert.match(
  viteConfig,
  /import\.meta\.env\.VITE_LENGRVIS_DESKTOP_API_TOKEN.*production|isProduction[\s\S]*VITE_LENGRVIS_DESKTOP_API_TOKEN/,
  "production renderer build must strip VITE_LENGRVIS_DESKTOP_API_TOKEN"
);

const settingsSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "renderer", "components", "settings", "LocalModelInstaller.tsx"),
  "utf8"
);
assert.match(settingsSource, /subscribeInstallModelProgressSocket/, "install progress should use the shared subscribe helper");
assert.doesNotMatch(settingsSource, /new WebSocket\(buildInstallModelWebSocketUrl/, "install progress must not directly create protected WebSockets");
assert.doesNotMatch(
  settingsSource,
  /window\.lengrvis\.realtime\.subscribe\(\s*\{\s*endpoint:\s*path/,
  "Electron install progress must not open the protected install WebSocket through the generic desktop bridge"
);
assert.match(settingsSource, /function isInstallModelWebOnlyDevFallbackEnabled[\s\S]*import\.meta\.env\.DEV/, "install progress fallback must be web-only dev gated");

const preloadSource = fs.readFileSync(path.join(__dirname, "..", "src", "preload", "preload.ts"), "utf8");
assert.doesNotMatch(preloadSource, /LENGRVIS_DESKTOP_API_TOKEN/, "preload must not expose or read the desktop token");

console.log("desktop WebSocket token smoke passed");
