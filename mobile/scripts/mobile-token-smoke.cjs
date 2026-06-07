const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const clientSourcePath = path.resolve(__dirname, "../src/api/client.ts");
const authSourcePath = path.resolve(__dirname, "../src/store/auth.ts");

function loadClient(fetchImpl) {
  const source = fs.readFileSync(clientSourcePath, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      strict: true,
    },
  }).outputText;

  const sandbox = {
    exports: {},
    module: { exports: {} },
    require,
    URL,
    fetch: fetchImpl,
  };
  sandbox.exports = sandbox.module.exports;
  vm.runInNewContext(compiled, sandbox, { filename: clientSourcePath });
  return sandbox.module.exports;
}

function loadAuth(client, storage) {
  const source = fs.readFileSync(authSourcePath, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      strict: true,
    },
  }).outputText;

  const sandbox = {
    exports: {},
    module: { exports: {} },
    require: (id) => {
      if (id === "../api/client") return client;
      if (id === "@react-native-async-storage/async-storage") return { __esModule: true, default: storage.asyncStorage, ...storage.asyncStorage };
      if (id === "expo-secure-store") return storage.secureStore;
      return require(id);
    },
  };
  sandbox.exports = sandbox.module.exports;
  vm.runInNewContext(compiled, sandbox, { filename: authSourcePath });
  return sandbox.module.exports;
}

function makeSession(client, baseUrl, token = "session-token") {
  const security = client.describeBaseUrlSecurity(baseUrl);
  return {
    baseUrl: security.normalizedBaseUrl,
    baseUrlSecurity: security,
    deviceId: "device-1",
    token,
  };
}

function makeStorage() {
  const asyncMap = new Map();
  const secureMap = new Map();
  return {
    asyncMap,
    secureMap,
    asyncStorage: {
      getItem: async (key) => asyncMap.get(key) ?? null,
      setItem: async (key, value) => {
        asyncMap.set(key, String(value));
      },
      removeItem: async (key) => {
        asyncMap.delete(key);
      },
    },
    secureStore: {
      getItemAsync: async (key) => secureMap.get(key) ?? null,
      setItemAsync: async (key, value) => {
        secureMap.set(key, String(value));
      },
      deleteItemAsync: async (key) => {
        secureMap.delete(key);
      },
    },
  };
}

async function main() {
  const fetchCalls = [];
  const client = loadClient(async (url, init) => {
    fetchCalls.push({ url: String(url), init });
    const isLanHttp = String(url).startsWith("http://192.168.1.20:8000");
    const isRealBackendTransportShape = String(url).startsWith("https://lengrvis.local:8443");
    const transportSecurity = isRealBackendTransportShape
      ? {
          status: "https_ready",
          scheme: "https",
          origin: "https://lengrvis.local:8443",
          https_enabled: true,
          tls_ready: true,
          requires_trust: true,
          trust_required: true,
          trust_model: "local_certificate",
        }
      : undefined;
    return {
      ok: true,
      status: 200,
      json: async () => ({
        token: "paired-token",
        token_type: "Bearer",
        device_id: "device-1",
        expires_in: 3600,
        server: isLanHttp
          ? { host: "192.168.1.20", port: 8000, protocol: "http", url: "http://192.168.1.20:8000" }
          : isRealBackendTransportShape
            ? { host: "lengrvis.local", port: 8443, scheme: "https", origin: "https://lengrvis.local:8443", transport_security: transportSecurity }
            : { host: "example.test", port: 8443, protocol: "https", url: "https://example.test:8443", base_url: "https://example.test:8443" },
        ...(transportSecurity ? { transport_security: transportSecurity, https_enabled: true, trust_required: true, server_origin: "https://lengrvis.local:8443" } : {}),
        ...(isRealBackendTransportShape
          ? {}
          : {
              security: isLanHttp
                ? {
                    transport: { http_scheme: "http", websocket_scheme: "ws", tls_enabled: false },
                    tls: { enabled: false, trust_status: "not_enabled" },
                  }
                : {
                    transport: { http_scheme: "https", websocket_scheme: "wss", tls_enabled: true },
                    tls: {
                      enabled: true,
                      trust_status: "requires_trust",
                      requires_trust: true,
                      self_signed: true,
                      fingerprint_sha256: "aabbccddeeff00112233445566778899",
                      subject: "CN=Lengrvis Local",
                      issuer: "CN=Lengrvis Local",
                    },
                  },
            }),
      }),
    };
  });

  assert.equal(client.normalizeBaseUrl("192.168.1.20:8000/"), "http://192.168.1.20:8000");
  assert.equal(client.normalizeBaseUrl("https://Example.test:8443/"), "https://example.test:8443");
  assert.throws(() => client.normalizeBaseUrl("ftp://example.test"), /http:\/\/.*https:\/\//);

  const httpsSecurity = client.describeBaseUrlSecurity("https://example.test:8443/");
  assert.equal(httpsSecurity.kind, "https");
  assert.equal(httpsSecurity.isHttps, true);
  assert.equal(httpsSecurity.isInsecureLan, false);

  const loopbackSecurity = client.describeBaseUrlSecurity("http://127.0.0.1:8000");
  assert.equal(loopbackSecurity.kind, "loopbackHttp");
  assert.equal(loopbackSecurity.isLoopback, true);
  assert.equal(loopbackSecurity.requiresExplicitAllow, false);
  assert.equal(client.isLoopbackBaseUrl("http://[::1]:8000"), true);

  const lanSecurity = client.describeBaseUrlSecurity("http://192.168.1.20:8000");
  assert.equal(lanSecurity.kind, "insecureLan");
  assert.equal(lanSecurity.isInsecureLan, true);
  assert.equal(lanSecurity.requiresExplicitAllow, true);
  assert.match(lanSecurity.warning, /明文传输/);
  assert.throws(() => client.assertSafeBaseUrl("http://192.168.1.20:8000"), (error) => {
    assert.equal(error.name, "InsecureLanBaseUrlError");
    assert.equal(error.security.kind, "insecureLan");
    return true;
  });
  assert.equal(client.assertSafeBaseUrl("http://192.168.1.20:8000", { allowInsecureLan: true }).kind, "insecureLan");

  await assert.rejects(
    () => client.pairWithBackend("http://192.168.1.20:8000", "abc123", "Phone"),
    (error) => error.name === "InsecureLanBaseUrlError" && error.security.kind === "insecureLan",
  );
  assert.equal(fetchCalls.length, 0, "blocked insecure LAN pair attempts must not reach fetch");

  const paired = await client.pairWithBackend("https://example.test:8443/", "abc123", "Phone");
  assert.equal(paired.baseUrl, "https://example.test:8443");
  assert.equal(paired.baseUrlSecurity.kind, "https");
  assert.equal(paired.server.protocol, "https");
  assert.equal(paired.security.transport.httpScheme, "https");
  assert.equal(paired.security.transport.webSocketScheme, "wss");
  assert.equal(paired.security.tls.enabled, true);
  assert.equal(paired.security.tls.requiresTrust, true);
  assert.equal(paired.security.tls.isSelfSigned, true);
  assert.equal(paired.baseUrlSecurity.requiresTlsTrust, true);
  assert.equal(paired.baseUrlSecurity.serverTls.fingerprintSha256, "AABBCCDDEEFF00112233445566778899");
  assert.equal(client.formatTlsFingerprint(paired.baseUrlSecurity.serverTls.fingerprintSha256), "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99");
  assert.match(paired.baseUrlSecurity.warning, /自签|证书/);
  assert.equal(fetchCalls[0].url, "https://example.test:8443/api/pair/confirm");
  assert.equal(JSON.parse(fetchCalls[0].init.body).device_name, "Phone");

  const backendShapePaired = await client.pairWithBackend("https://lengrvis.local:8443/", "def456", "Phone");
  assert.equal(backendShapePaired.server.scheme, "https");
  assert.equal(backendShapePaired.server.origin, "https://lengrvis.local:8443");
  assert.equal(backendShapePaired.server.transportSecurity.transport.httpScheme, "https");
  assert.equal(backendShapePaired.server.transportSecurity.transport.advertisedBaseUrl, "https://lengrvis.local:8443");
  assert.equal(backendShapePaired.security.transport.httpScheme, "https");
  assert.equal(backendShapePaired.security.transport.advertisedBaseUrl, "https://lengrvis.local:8443");
  assert.equal(backendShapePaired.security.tls.enabled, true);
  assert.equal(backendShapePaired.security.tls.requiresTrust, true);
  assert.equal(backendShapePaired.security.tls.trustStatus, "requires_trust");
  assert.equal(backendShapePaired.baseUrlSecurity.requiresTlsTrust, true);
  assert.equal(backendShapePaired.baseUrlSecurity.serverTls.enabled, true);

  const lanPaired = await client.pairWithBackend("http://192.168.1.20:8000", "abc123", "Phone", {
    allowInsecureLan: true,
  });
  assert.equal(lanPaired.baseUrlSecurity.kind, "insecureLan");
  assert.equal(lanPaired.baseUrlSecurity.backendTlsEnabled, false);
  assert.equal(lanPaired.security.tls.trustStatus, "not_enabled");
  assert.equal(fetchCalls[2].url, "http://192.168.1.20:8000/api/pair/confirm");

  const httpsSession = makeSession(client, "https://example.test:8443", "session-token");
  const screenInfo = client.remoteScreenWebSocketConnectionInfo(httpsSession);
  assert.equal(screenInfo.url, "wss://example.test:8443/ws/remote/screen");
  assert.equal(JSON.stringify(screenInfo.protocols), JSON.stringify(["lengrvis.mobile.token.session-token"]));
  assert.equal(screenInfo.warning, undefined);
  assert.doesNotMatch(screenInfo.url, /token/);
  assert.equal(client.remoteScreenWebSocketUrl(httpsSession), screenInfo.url);

  const lanSession = makeSession(client, "http://192.168.1.20:8000", "session-token");
  const approvalInfo = client.approvalWebSocketConnectionInfo(lanSession);
  assert.equal(approvalInfo.url, "ws://192.168.1.20:8000/ws/mobile/approvals");
  assert.equal(JSON.stringify(approvalInfo.protocols), JSON.stringify(["lengrvis.mobile.token.session-token"]));
  assert.equal(approvalInfo.security.kind, "insecureLan");
  assert.match(approvalInfo.warning, /明文传输/);
  assert.doesNotMatch(approvalInfo.url, /session-token|token=/);

  const inputInfo = client.remoteInputWebSocketConnectionInfo(lanSession, "grant-token");
  assert.equal(inputInfo.url, "ws://192.168.1.20:8000/ws/remote/input");
  assert.equal(JSON.stringify(inputInfo.protocols), JSON.stringify(["lengrvis.mobile.token.grant-token"]));
  assert.doesNotMatch(inputInfo.url, /grant-token|token=/);
  assert.equal(JSON.stringify(client.mobileTokenWebSocketProtocols("grant-token")), JSON.stringify(["lengrvis.mobile.token.grant-token"]));

  const migratedStorage = makeStorage();
  const migratedAuth = loadAuth(client, migratedStorage);
  migratedStorage.asyncMap.set(
    "lengrvis.mobile.session",
    JSON.stringify({
      baseUrl: paired.baseUrl,
      baseUrlSecurity: paired.baseUrlSecurity,
      deviceId: paired.deviceId,
      server: paired.server,
      security: paired.security,
      token: "legacy-token",
    }),
  );
  const migrated = await migratedAuth.loadSession();
  assert.equal(migrated.token, "legacy-token");
  assert.equal(migrated.baseUrl, "https://example.test:8443");
  assert.equal(migrated.baseUrlSecurity.requiresTlsTrust, true);
  assert.equal(migrated.baseUrlSecurity.serverTls.fingerprintSha256, "AABBCCDDEEFF00112233445566778899");
  assert.equal(migrated.security.tls.trustStatus, "requires_trust");
  assert.equal(migrated.server.baseUrl, "https://example.test:8443");
  assert.equal(migratedStorage.secureMap.get("lengrvis.mobile.session.token"), "legacy-token");
  assert.doesNotMatch(migratedStorage.asyncMap.get("lengrvis.mobile.session"), /legacy-token/);

  await migratedAuth.saveSession(lanPaired);
  const storedLan = JSON.parse(migratedStorage.asyncMap.get("lengrvis.mobile.session"));
  assert.equal(storedLan.baseUrlSecurity.kind, "insecureLan");
  assert.equal(storedLan.baseUrlSecurity.backendTlsEnabled, false);
  assert.equal(storedLan.security.tls.trustStatus, "not_enabled");
  assert.doesNotMatch(migratedStorage.asyncMap.get("lengrvis.mobile.session"), /paired-token/);
}

main()
  .then(() => console.log("Mobile token smoke passed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
