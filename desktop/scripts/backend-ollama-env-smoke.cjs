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
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mavris-backend-env-"));
const resources = path.join(tmpRoot, "resources");
const backendDir = path.join(resources, "backend");
const ollamaDir = path.join(resources, "ollama");
const modelsDir = path.join(resources, "ollama-models");
const manifestPath = path.join(resources, "ollama-bundle-manifest.json");
const backendExe = path.join(backendDir, process.platform === "win32" ? "backend.exe" : "backend");
let spawnCall = null;

fs.mkdirSync(backendDir, { recursive: true });
fs.mkdirSync(ollamaDir, { recursive: true });
fs.mkdirSync(modelsDir, { recursive: true });
fs.writeFileSync(backendExe, "fake backend");
fs.writeFileSync(manifestPath, "{}");
process.env.MAVRIS_BACKEND_SERVICE_DISABLED = "1";
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

global.fetch = async () => ({
  ok: false,
  json: async () => ({})
});

(async () => {
  try {
    const { BackendProcessManager } = require("../dist/main/backendProcess.js");
    const manager = new BackendProcessManager();
    await manager.start();

    assert.ok(spawnCall, "backend process should be spawned");
    assert.equal(spawnCall.command, backendExe);
    assert.equal(spawnCall.options.env.MAVRIS_BUNDLED_OLLAMA_DIR, ollamaDir);
    assert.equal(spawnCall.options.env.MARVIS_BUNDLED_OLLAMA_DIR, ollamaDir);
    assert.equal(spawnCall.options.env.MAVRIS_BUNDLED_OLLAMA_MODELS_DIR, modelsDir);
    assert.equal(spawnCall.options.env.MARVIS_BUNDLED_OLLAMA_MODELS_DIR, modelsDir);
    assert.equal(spawnCall.options.env.MAVRIS_OLLAMA_BUNDLE_MANIFEST, manifestPath);
    assert.equal(spawnCall.options.env.MARVIS_OLLAMA_BUNDLE_MANIFEST, manifestPath);
    assert.equal(spawnCall.options.env.OLLAMA_MODELS, modelsDir);

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
