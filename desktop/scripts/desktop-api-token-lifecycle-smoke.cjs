const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");

const originalLoad = Module._load;
const originalCwd = process.cwd();
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "lengrvis-desktop-token-"));

Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "electron") {
    return {
      app: {
        getAppPath: () => tmpRoot
      }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const {
  DESKTOP_API_TOKEN_FILE,
  resolveBackendConfigDir,
  resolveBackendDataDir,
  resolveDesktopApiToken
} = require("../dist/main/desktopApiToken.js");

function mkdirp(dir) {
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function readSecret(dataDir) {
  return fs.readFileSync(path.join(dataDir, DESKTOP_API_TOKEN_FILE), "utf8").trim();
}

try {
  const serviceRoot = mkdirp(path.join(tmpRoot, "service-root"));
  const serviceDataDir = path.join(serviceRoot, "service-data");
  fs.writeFileSync(path.join(serviceRoot, "config.yaml"), "paths:\n  data_dir: ./service-data\n");
  mkdirp(serviceDataDir);
  fs.writeFileSync(path.join(serviceDataDir, DESKTOP_API_TOKEN_FILE), "service-secret\n");

  const existing = resolveDesktopApiToken({
    configDir: serviceRoot,
    env: { LENGRVIS_DESKTOP_API_TOKEN: "wrong-env-secret" },
    generateToken: () => "wrong-generated-secret"
  });

  assert.equal(existing.source, "file");
  assert.equal(existing.token, "service-secret");
  assert.equal(existing.dataDir, serviceDataDir);

  const envRoot = mkdirp(path.join(tmpRoot, "env-root"));
  const envDataDir = path.join(envRoot, "relative-data");
  const envBacked = resolveDesktopApiToken({
    configDir: envRoot,
    env: {
      LENGRVIS_DATA_DIR: "relative-data",
      LENGRVIS_DESKTOP_API_TOKEN: "env-secret"
    },
    generateToken: () => "wrong-generated-secret"
  });

  assert.equal(envBacked.source, "env");
  assert.equal(envBacked.token, "env-secret");
  assert.equal(envBacked.dataDir, envDataDir);
  assert.equal(readSecret(envDataDir), "env-secret");

  const createdRoot = mkdirp(path.join(tmpRoot, "created-root"));
  const created = resolveDesktopApiToken({
    configDir: createdRoot,
    env: {},
    generateToken: () => "created-secret"
  });
  const reused = resolveDesktopApiToken({
    configDir: createdRoot,
    env: {},
    generateToken: () => "different-secret"
  });

  assert.equal(created.source, "created");
  assert.equal(created.token, "created-secret");
  assert.equal(reused.source, "file");
  assert.equal(reused.token, "created-secret");
  assert.equal(readSecret(created.dataDir), "created-secret");

  const emptyRoot = mkdirp(path.join(tmpRoot, "empty-root"));
  const emptyDataDir = mkdirp(resolveBackendDataDir({ configDir: emptyRoot, env: {} }));
  fs.writeFileSync(path.join(emptyDataDir, DESKTOP_API_TOKEN_FILE), "");
  const repaired = resolveDesktopApiToken({
    configDir: emptyRoot,
    env: {},
    generateToken: () => "repaired-secret"
  });

  assert.equal(repaired.source, "created");
  assert.equal(repaired.token, "repaired-secret");
  assert.equal(readSecret(emptyDataDir), "repaired-secret");

  const projectRoot = mkdirp(path.join(tmpRoot, "project"));
  mkdirp(path.join(projectRoot, "backend", "app"));
  const nestedDesktopDir = mkdirp(path.join(projectRoot, "desktop", "dist"));
  fs.writeFileSync(path.join(projectRoot, "backend", "app", "config.py"), "# marker\n");
  process.chdir(nestedDesktopDir);
  const foundConfigDir = resolveBackendConfigDir({
    command: path.join(projectRoot, "desktop", "dist", "backend.exe"),
    env: {}
  });

  assert.equal(foundConfigDir, projectRoot);
  assert.equal(path.dirname(resolveBackendDataDir({ configDir: projectRoot, env: {} })), projectRoot);

  console.log("desktop API token lifecycle smoke passed");
} finally {
  Module._load = originalLoad;
  process.chdir(originalCwd);
  fs.rmSync(tmpRoot, { recursive: true, force: true });
}
