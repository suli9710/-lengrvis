const assert = require("node:assert/strict");
const Module = require("node:module");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const childProcess = require("node:child_process");

const originalLoad = Module._load;
const originalResourcesPath = process.resourcesPath;
const originalEnv = { ...process.env };
const originalSpawn = childProcess.spawn;
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "lengrvis-backend-env-"));
const resources = path.join(tmpRoot, "resources");
const backendDir = path.join(resources, "backend");
const ollamaDir = path.join(resources, "ollama");
const modelsDir = path.join(resources, "ollama-models");
const manifestPath = path.join(resources, "ollama-bundle-manifest.json");
const backendExe = path.join(backendDir, process.platform === "win32" ? "backend.exe" : "backend");
let spawnCall = null;
let runtimeModeRequest = null;

fs.mkdirSync(backendDir, { recursive: true });
fs.mkdirSync(ollamaDir, { recursive: true });
fs.mkdirSync(modelsDir, { recursive: true });
fs.writeFileSync(backendExe, "fake backend");
fs.writeFileSync(manifestPath, "{}");
process.env.LENGRVIS_BACKEND_SERVICE_DISABLED = "1";
process.env.LENGRVIS_CONFIG_DIR = tmpRoot;
process.env.LENGRVIS_DATA_DIR = path.join(tmpRoot, "data");
Object.defineProperty(process, "resourcesPath", {
  value: resources,
  configurable: true
});

Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "electron") {
    return {
      app: {
        getAppPath: () => path.join(resources, "app"),
        getPath: () => tmpRoot,
        isPackaged: true
      }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

childProcess.spawn = function patchedSpawn(command, args, options) {
  spawnCall = { command, args, options };
  return {
    killed: false,
    pid: 4242,
    stdout: { on() {} },
    stderr: { on() {} },
    once() {},
    kill() {}
  };
};

global.fetch = async (url, options = {}) => {
  const pathname = new URL(String(url)).pathname;
  if (pathname === "/api/runtime/foreground" || pathname === "/api/runtime/background") {
    runtimeModeRequest = { url: String(url), options };
    return {
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      clone() {
        return this;
      },
      json: async () => ({ detail: "guardian not ready" }),
      text: async () => "guardian not ready"
    };
  }
  return {
    ok: false,
    status: 503,
    statusText: "Service Unavailable",
    clone() {
      return this;
    },
    json: async () => ({}),
    text: async () => ""
  };
};

(async () => {
  try {
    const { BackendProcessManager } = require("../dist/main/backendProcess.js");
    const { unprotectLocalSecret } = require("../dist/main/localSecret.js");
    const manager = new BackendProcessManager();
    await manager.start();
    const desktopApiToken = manager.getDesktopApiToken();
    const storedDesktopApiToken = unprotectLocalSecret(
      fs.readFileSync(path.join(process.env.LENGRVIS_DATA_DIR, "desktop_api.secret"), "utf8").trim()
    );

    assert.ok(spawnCall, "backend process should be spawned");
    assert.equal(spawnCall.command, backendExe);
    assert.equal(spawnCall.options.env.LENGRVIS_BUNDLED_OLLAMA_DIR, ollamaDir);
    assert.equal(spawnCall.options.env.LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR, modelsDir);
    assert.equal(spawnCall.options.env.LENGRVIS_OLLAMA_BUNDLE_MANIFEST, manifestPath);
    assert.equal(spawnCall.options.env.OLLAMA_MODELS, modelsDir);
    assert.equal(spawnCall.options.env.LENGRVIS_DATA_DIR, process.env.LENGRVIS_DATA_DIR);
    assert.equal(spawnCall.options.env.LENGRVIS_DESKTOP_API_TOKEN, desktopApiToken);
    assert.equal(storedDesktopApiToken, desktopApiToken);

    const foregroundStatus = await manager.enterForeground("smoke_foreground");
    assert.equal(foregroundStatus.state, "starting", "failed health should remain starting instead of reporting a ready backend");
    assert.match(foregroundStatus.runtimeModeError, /503.*guardian not ready/);
    assert.match(foregroundStatus.message, /could not enter foreground runtime mode/);
    assert.ok(runtimeModeRequest, "foreground runtime mode should be attempted");
    assert.equal(runtimeModeRequest.options.headers["X-Lengrvis-Desktop-Token"], manager.getDesktopApiToken());

    runtimeModeRequest = null;
    const remoteBaseUrlManager = new BackendProcessManager({ baseUrl: "https://api.example.test" });
    const remoteForegroundStatus = await remoteBaseUrlManager.enterForeground("remote_foreground");
    assert.equal(remoteForegroundStatus.state, "starting", "non-loopback runtime mode guard should not report a ready backend without health");
    assert.match(remoteForegroundStatus.runtimeModeError, /loopback backend base URL/);
    assert.equal(runtimeModeRequest, null, "runtime mode guard must reject non-loopback backend before sending desktop token");

    console.log("Backend bundled Ollama env smoke passed");
  } finally {
    Module._load = originalLoad;
    childProcess.spawn = originalSpawn;
    process.env = originalEnv;
    if (originalResourcesPath !== undefined) {
      Object.defineProperty(process, "resourcesPath", {
        value: originalResourcesPath,
        configurable: true
      });
    }
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
