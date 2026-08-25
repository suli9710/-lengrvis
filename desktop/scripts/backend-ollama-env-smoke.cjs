const assert = require("node:assert/strict");
const Module = require("node:module");
const fs = require("node:fs");
const crypto = require("node:crypto");
const os = require("node:os");
const path = require("node:path");
const childProcess = require("node:child_process");

const originalLoad = Module._load;
const originalResourcesPath = process.resourcesPath;
const originalEnv = { ...process.env };
const originalSpawn = childProcess.spawn;
const originalExecFile = childProcess.execFile;
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "lengrvis-backend-env-"));
const resources = path.join(tmpRoot, "resources");
const backendDir = path.join(resources, "backend");
const ollamaDir = path.join(resources, "ollama");
const modelsDir = path.join(resources, "ollama-models");
const manifestPath = path.join(resources, "ollama-bundle-manifest.json");
const backendExe = path.join(backendDir, process.platform === "win32" ? "backend.exe" : "backend");
const userDataDir = path.join(tmpRoot, "user-data");
const hostileDataDir = path.join(tmpRoot, "hostile-data");
const hostileConfigDir = path.join(tmpRoot, "hostile-config");
const hostileBackend = path.join(tmpRoot, process.platform === "win32" ? "hostile.exe" : "hostile");
let spawnCall = null;
let spawnCount = 0;
let serviceProbeCount = 0;
let runtimeModeRequest = null;
let healthProofMode = "valid";
let healthProbe = null;

fs.mkdirSync(backendDir, { recursive: true });
fs.mkdirSync(ollamaDir, { recursive: true });
fs.mkdirSync(modelsDir, { recursive: true });
fs.mkdirSync(userDataDir, { recursive: true });
fs.mkdirSync(hostileDataDir, { recursive: true });
fs.mkdirSync(hostileConfigDir, { recursive: true });
fs.writeFileSync(backendExe, "fake backend");
fs.writeFileSync(hostileBackend, "hostile backend");
fs.writeFileSync(manifestPath, "{}");
process.env.LENGRVIS_BACKEND_SERVICE_DISABLED = "1";
process.env.LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS = "1";
process.env.LENGRVIS_ENV = "development";
process.env.LENGRVIS_TEST = "1";
process.env.LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL = "true";
process.env.LENGRVIS_STRICT_STATE_MACHINE = "false";
process.env.PYTEST_CURRENT_TEST = "hostile-parent::test";
process.env.LENGRVIS_CONFIG_DIR = hostileConfigDir;
process.env.LENGRVIS_DATA_DIR = hostileDataDir;
process.env.LENGRVIS_CONFIG_FILE = path.join(hostileConfigDir, "config.yaml");
process.env.LENGRVIS_ENV_FILE = path.join(hostileConfigDir, ".env");
process.env.LENGRVIS_DESKTOP_API_TOKEN = "hostile-parent-token";
process.env.LENGRVIS_NATIVE_CONFIRMATION_SECRET = "hostile-native-confirmation-secret";
process.env.LENGRVIS_APPROVAL_HMAC_SECRET = "hostile-approval-secret";
process.env["LENGRVIS_" + "AUDIT_HMAC_SECRET"] = "hostile-audit-secret";
process.env.LENGRVIS_AUDIT_HMAC_SECRET_FILE = path.join(hostileDataDir, "audit.secret");
process.env.LENGRVIS_JWT_SECRET = "hostile-mobile-jwt-secret";
process.env.LENGRVIS_BACKEND_COMMAND = hostileBackend;
process.env.LENGRVIS_BACKEND_ARGS = "--hostile-arg";
process.env.LENGRVIS_BACKEND_CWD = hostileConfigDir;
process.env.LENGRVIS_BACKEND_URL = "https://hostile.example.test";
process.env.LENGRVIS_BACKEND_HOST = "0.0.0.0";
process.env.LENGRVIS_BACKEND_PORT = "9443";
process.env.LENGRVIS_LAN_TLS_ENABLED = "true";
process.env.LENGRVIS_LAN_TLS_AUTO = "true";
process.env.LENGRVIS_LAN_PUBLIC_BASE_URL = "https://hostile.example.test:9443";
process.env.LENGRVIS_LAN_TLS_CERT_FILE = path.join(hostileConfigDir, "hostile-cert.pem");
process.env.LENGRVIS_LAN_TLS_KEY_FILE = path.join(hostileConfigDir, "hostile-key.pem");
process.env.LENGRVIS_ALLOW_LAN_DESKTOP_API = "true";
process.env.LENGRVIS_TRUSTED_PROXY_IPS = "0.0.0.0/0";
process.env.LENGRVIS_TRUSTED_PROXIES = "0.0.0.0/0";
process.env.LENGRVIS_COMMERCIAL_RELEASE = "false";
process.env.LENGRVIS_LICENSE_PUBLIC_KEY = "ed25519:hostile";
Object.defineProperty(process, "resourcesPath", {
  value: resources,
  configurable: true
});

Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "electron") {
    return {
      app: {
        getAppPath: () => path.join(resources, "app"),
        getPath: (name) => name === "userData" ? userDataDir : tmpRoot,
        isPackaged: true
      },
      safeStorage: {
        isEncryptionAvailable: () => true,
        getSelectedStorageBackend: () => "mock_keychain",
        encryptString: (value) => Buffer.from(value, "utf8"),
        decryptString: (buffer) => Buffer.from(buffer).toString("utf8")
      }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

childProcess.spawn = function patchedSpawn(command, args, options) {
  spawnCount += 1;
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

childProcess.execFile = function patchedExecFile(command, args, options, callback) {
  const done = typeof options === "function" ? options : callback;
  if (command === "sc.exe" && typeof done === "function") {
    serviceProbeCount += 1;
    done(Object.assign(new Error("service does not exist"), { code: 1060 }), "", "FAILED 1060: service does not exist");
    return { kill() {} };
  }
  return originalExecFile(command, args, options, callback);
};

global.fetch = async (url, options = {}) => {
  const parsedUrl = new URL(String(url));
  const pathname = parsedUrl.pathname;
  if (pathname === "/health") {
    const challenge = parsedUrl.searchParams.get("desktop_challenge") ?? "";
    healthProbe = { url: String(url), options, challenge };
    const desktopProof = healthProofMode === "valid"
      ? crypto.createHmac("sha256", process.env.LENGRVIS_DESKTOP_API_TOKEN ?? "").update(challenge, "utf8").digest("hex")
      : undefined;
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      clone() {
        return this;
      },
      json: async () => ({ status: "ok", ...(desktopProof ? { desktop_proof: desktopProof } : {}) }),
      text: async () => ""
    };
  }
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
    const nativeConfirmationPublicKey = manager.getNativeConfirmationPublicKey();
    const storedDesktopApiToken = unprotectLocalSecret(
      fs.readFileSync(path.join(userDataDir, "desktop_api.secret"), "utf8").trim()
    );

    assert.ok(spawnCall, "backend process should be spawned");
    assert.equal(spawnCall.command, backendExe);
    assert.deepEqual(spawnCall.args, []);
    assert.equal(spawnCall.options.cwd, backendDir);
    assert.equal(spawnCall.options.env.LENGRVIS_BUNDLED_OLLAMA_DIR, ollamaDir);
    assert.equal(spawnCall.options.env.LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR, modelsDir);
    assert.equal(spawnCall.options.env.LENGRVIS_OLLAMA_BUNDLE_MANIFEST, manifestPath);
    assert.equal(spawnCall.options.env.OLLAMA_MODELS, modelsDir);
    assert.equal(spawnCall.options.env.LENGRVIS_ENV, "production");
    assert.equal(spawnCall.options.env.LENGRVIS_TEST, "0");
    assert.equal(spawnCall.options.env.LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL, "false");
    assert.equal(spawnCall.options.env.LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS, "false");
    assert.equal(spawnCall.options.env.LENGRVIS_STRICT_STATE_MACHINE, "true");
    assert.equal(spawnCall.options.env.PYTEST_CURRENT_TEST, "");
    assert.equal(spawnCall.options.env.LENGRVIS_COMMERCIAL_RELEASE, "true");
    assert.equal(spawnCall.options.env.LENGRVIS_ACTIVATION_BASE_URL, "https://agent.lengzhehao.com");
    assert.equal(
      spawnCall.options.env.LENGRVIS_LICENSE_PUBLIC_KEY,
      "ed25519:0LY7FXJpX494464DDN_vqSbqgCMX4sAj2iwf5gmC5c4"
    );
    assert.equal(spawnCall.options.env.LENGRVIS_CONFIG_DIR, backendDir);
    assert.equal(spawnCall.options.env.LENGRVIS_DATA_DIR, userDataDir);
    assert.equal(spawnCall.options.env.LENGRVIS_BACKEND_URL, "http://127.0.0.1:8000");
    assert.equal(spawnCall.options.env.LENGRVIS_BACKEND_HOST, "127.0.0.1");
    assert.equal(spawnCall.options.env.LENGRVIS_BACKEND_PORT, "8000");
    assert.equal(spawnCall.options.env.LENGRVIS_BACKEND_SERVICE_DISABLED, "1");
    assert.equal(spawnCall.options.env.LENGRVIS_LAN_TLS_ENABLED, "false");
    assert.equal(spawnCall.options.env.LENGRVIS_LAN_TLS_AUTO, "false");
    assert.equal(spawnCall.options.env.LENGRVIS_LAN_PUBLIC_BASE_URL, "");
    assert.equal(spawnCall.options.env.LENGRVIS_LAN_TLS_CERT_FILE, "");
    assert.equal(spawnCall.options.env.LENGRVIS_LAN_TLS_KEY_FILE, "");
    assert.equal(spawnCall.options.env.LENGRVIS_ALLOW_LAN_DESKTOP_API, "false");
    assert.equal(spawnCall.options.env.LENGRVIS_TRUSTED_PROXY_IPS, "");
    assert.equal(spawnCall.options.env.LENGRVIS_TRUSTED_PROXIES, "");
    assert.equal(spawnCall.options.env.LENGRVIS_DESKTOP_API_TOKEN, desktopApiToken);
    assert.notEqual(desktopApiToken, "hostile-parent-token");
    assert.equal(spawnCall.options.env.LENGRVIS_NATIVE_CONFIRMATION_PUBLIC_KEY, nativeConfirmationPublicKey);
    assert.match(spawnCall.options.env.LENGRVIS_NATIVE_CONFIRMATION_PUBLIC_KEY, /^[A-Za-z0-9_-]+$/);
    assert.equal(spawnCall.options.env.LENGRVIS_NATIVE_CONFIRMATION_SECRET, undefined);
    assert.ok(healthProbe, "backend startup should issue an identity challenge before trusting health");
    assert.match(healthProbe.challenge, /^[A-Za-z0-9_-]{16,128}$/);
    assert.equal(healthProbe.options.headers, undefined, "health identity challenge must not send the desktop token");
    assert.equal(storedDesktopApiToken, desktopApiToken);
    assert.equal(process.env.LENGRVIS_ENV, "production");
    assert.equal(process.env.LENGRVIS_TEST, "0");
    assert.equal(process.env.LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL, "false");
    assert.equal(process.env.LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS, "false");
    assert.equal(process.env.LENGRVIS_STRICT_STATE_MACHINE, "true");
    assert.equal(process.env.PYTEST_CURRENT_TEST, undefined);
    assert.equal(process.env.LENGRVIS_CONFIG_DIR, backendDir);
    assert.equal(process.env.LENGRVIS_DATA_DIR, userDataDir);
    assert.equal(process.env.LENGRVIS_DESKTOP_API_TOKEN, desktopApiToken);
    assert.equal(process.env.LENGRVIS_BACKEND_URL, "http://127.0.0.1:8000");
    assert.equal(process.env.LENGRVIS_BACKEND_HOST, "127.0.0.1");
    assert.equal(process.env.LENGRVIS_BACKEND_PORT, "8000");
    assert.equal(process.env.LENGRVIS_BACKEND_SERVICE_DISABLED, "1");
    assert.equal(process.env.LENGRVIS_LAN_TLS_ENABLED, "false");
    assert.equal(process.env.LENGRVIS_LAN_TLS_AUTO, "false");
    assert.equal(process.env.LENGRVIS_ALLOW_LAN_DESKTOP_API, "false");
    assert.equal(process.env.LENGRVIS_LAN_PUBLIC_BASE_URL, undefined);
    assert.equal(process.env.LENGRVIS_LAN_TLS_CERT_FILE, undefined);
    assert.equal(process.env.LENGRVIS_LAN_TLS_KEY_FILE, undefined);
    assert.equal(process.env.LENGRVIS_TRUSTED_PROXY_IPS, undefined);
    assert.equal(process.env.LENGRVIS_TRUSTED_PROXIES, undefined);
    assert.equal(serviceProbeCount, 0, "packaged desktop must not attach to an ambient Windows Service");
    assert.equal(process.env.LENGRVIS_BACKEND_COMMAND, undefined);
    assert.equal(process.env.LENGRVIS_BACKEND_ARGS, undefined);
    assert.equal(process.env.LENGRVIS_BACKEND_CWD, undefined);
    assert.equal(process.env.LENGRVIS_CONFIG_FILE, undefined);
    assert.equal(process.env.LENGRVIS_ENV_FILE, undefined);
    assert.equal(process.env.LENGRVIS_NATIVE_CONFIRMATION_SECRET, undefined);
    assert.equal(process.env.LENGRVIS_APPROVAL_HMAC_SECRET, undefined);
    assert.equal(process.env.LENGRVIS_AUDIT_HMAC_SECRET, undefined);
    assert.equal(process.env.LENGRVIS_AUDIT_HMAC_SECRET_FILE, undefined);
    assert.equal(process.env.LENGRVIS_JWT_SECRET, undefined);
    assert.equal(
      fs.readFileSync(path.join(userDataDir, "native_confirmation_public.key"), "utf8").trim(),
      nativeConfirmationPublicKey
    );
    assert.equal(fs.existsSync(path.join(userDataDir, "native_confirmation.secret")), false);
    assert.equal(fs.existsSync(path.join(hostileDataDir, "desktop_api.secret")), false);

    const reusedManager = new BackendProcessManager();
    assert.equal(reusedManager.getNativeConfirmationPublicKey(), nativeConfirmationPublicKey);

    const concurrentManager = new BackendProcessManager();
    const spawnCountBeforeConcurrentStart = spawnCount;
    await Promise.all([concurrentManager.start(), concurrentManager.start()]);
    assert.equal(
      spawnCount - spawnCountBeforeConcurrentStart,
      1,
      "concurrent start requests must share one backend process launch"
    );

    const foregroundStatus = await manager.enterForeground("smoke_foreground");
    assert.equal(foregroundStatus.state, "running", "authenticated health should keep the backend available");
    assert.match(foregroundStatus.runtimeModeError, /503.*guardian not ready/);
    assert.match(foregroundStatus.message, /could not enter foreground runtime mode/);
    assert.ok(runtimeModeRequest, "foreground runtime mode should be attempted");
    assert.equal(runtimeModeRequest.options.headers["X-Lengrvis-Desktop-Token"], manager.getDesktopApiToken());

    runtimeModeRequest = null;
    const remoteBaseUrlManager = new BackendProcessManager({ baseUrl: "https://api.example.test" });
    const remoteForegroundStatus = await remoteBaseUrlManager.enterForeground("remote_foreground");
    assert.equal(remoteForegroundStatus.state, "running", "authenticated health can remain available while runtime mode is blocked");
    assert.match(remoteForegroundStatus.runtimeModeError, /loopback backend base URL/);
    assert.equal(runtimeModeRequest, null, "runtime mode guard must reject non-loopback backend before sending desktop token");

    healthProofMode = "missing";
    const untrustedManager = new BackendProcessManager();
    const untrustedStatus = await untrustedManager.start();
    assert.equal(untrustedStatus.health.identityVerified, false);
    assert.equal(untrustedStatus.state, "starting");
    assert.equal(untrustedManager.getDesktopApiToken(), "", "desktop token must remain unavailable before identity proof");

    console.log("Backend bundled Ollama env smoke passed");
  } finally {
    Module._load = originalLoad;
    childProcess.spawn = originalSpawn;
    childProcess.execFile = originalExecFile;
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
