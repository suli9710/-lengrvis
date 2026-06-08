const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  acceptWebSocketUpgrade,
  assertAcceptedWebSocket,
  assertJsonRequest,
  connectWebSocket,
  jsonResponse,
  loadMobileClient,
  loadTsModule,
  mobilePath,
  rejectWebSocketUpgrade,
  startHttpWsSmokeServer,
} = require("./behavior-smoke-helpers.cjs");

function loadAuth(client, storage) {
  return loadTsModule(mobilePath("src/store/auth.ts"), {
    require: (id) => {
      if (id === "../api/client") return client;
      if (id === "@react-native-async-storage/async-storage") return { __esModule: true, default: storage.asyncStorage, ...storage.asyncStorage };
      if (id === "expo-secure-store") return storage.secureStore;
      return require(id);
    },
  });
}

function loadPairingPayload(client) {
  return loadTsModule(mobilePath("src/api/pairingPayload.ts"), {
    require: (id) => {
      if (id === "./client") return client;
      return require(id);
    },
  });
}

function loadDesktopPairingPayload() {
  return loadTsModule(path.resolve(__dirname, "..", "..", "desktop", "src", "shared", "mobilePairingPayload.ts"));
}

function assertSourceIncludes(source, expected, message) {
  assert.ok(source.includes(expected), `${message}: expected source to include ${JSON.stringify(expected)}`);
}

function assertSourceMatches(source, pattern, message) {
  assert.match(source, pattern, message);
}

function assertPairScreenQrSourceAssertions() {
  const source = fs.readFileSync(mobilePath("src/screens/PairScreen.tsx"), "utf8");
  assertSourceMatches(
    source,
    /import\s+\{[\s\S]*\bCameraView\b[\s\S]*\buseCameraPermissions\b[\s\S]*\bBarcodeScanningResult\b[\s\S]*\}\s+from "expo-camera";/,
    "PairScreen must import the Expo camera scanner surface, permission hook, and scan result type",
  );
  assertSourceIncludes(
    source,
    "const [cameraPermission, requestCameraPermission] = useCameraPermissions();",
    "PairScreen must request camera permission through useCameraPermissions",
  );
  assertSourceIncludes(source, "requestCameraPermission()", "Opening the scanner must request camera permission when needed");
  assertSourceIncludes(
    source,
    "setFailure(cameraPermissionFailureNotice(nextPermission.canAskAgain));",
    "Denied camera permission must use beginner-readable recovery copy",
  );
  assertSourceIncludes(source, "scanLockedRef", "Scanner must synchronously lock repeated native scan callbacks");
  assertSourceIncludes(source, "cameraUnavailableFailureNotice()", "Camera open or mount failures must keep a paste fallback");

  assertSourceMatches(
    source,
    /const handleBarcodeScanned = \(result: BarcodeScanningResult\) => \{[\s\S]*const payload = parsePairingPayload\(result\.data\);[\s\S]*setPairingPayload\(result\.data\);[\s\S]*applyPayload\(payload\);[\s\S]*setFailure\(pairingFailureNotice\(currentError, undefined, "scan"\)\);[\s\S]*\n  \};/,
    "Barcode scan handler must parse the scanned QR payload data and apply that exact payload",
  );
  assertSourceIncludes(
    source,
    '<PairingScanner visible={isScanning} scanLocked={scanLocked} onClose={closeScanner} onScanned={handleBarcodeScanned} />',
    "PairScreen must wire the scanner result callback to the barcode handler",
  );
  assertSourceMatches(
    source,
    /<CameraView[\s\S]*barcodeScannerSettings=\{\{ barcodeTypes: \["qr"\] \}\}[\s\S]*facing="back"[\s\S]*onMountError=\{\(\) => onClose\(cameraUnavailableFailureNotice\(\)\)\}[\s\S]*onBarcodeScanned=\{scanLocked \? undefined : onScanned\}[\s\S]*\/>/,
    "PairingScanner must render a rear CameraView with QR-only scanning, mount failure handling, and the scan callback",
  );

  const beginnerCopy = [
    "打开相机扫码",
    "请求相机权限",
    "无法打开相机",
    "需要在系统设置打开相机",
    "需要相机权限",
    "允许相机权限",
    "粘贴电脑端二维码内容或配对信息",
    "扫码失败时也可以直接粘贴",
    "二维码里的电脑地址不可用",
    "二维码缺少电脑地址",
    "没有识别到 Lengrvis 配对二维码",
    "请对准电脑端 Lengrvis 配对页的二维码",
    "输入电脑地址和 6 位配对码",
    "等待安全配对信息",
    "重新生成配对码",
    "需要安全连接",
    "普通网络地址不能直接连接",
    "电脑指纹",
    "需要确认这台电脑",
    "手机还没有和这台电脑建立安全连接",
    "电脑端未打开",
  ];
  for (const copy of beginnerCopy) {
    assertSourceIncludes(source, copy, `PairScreen beginner copy must explain ${copy}`);
  }

  assertSourceIncludes(source, 'testID="pair-open-scanner-button"', "Scan entry must have a stable test id");
  assertSourceIncludes(source, 'testID="pairing-scanner-camera"', "Camera view must have a stable test id");
  assertSourceIncludes(source, 'testID="pair-failure-notice"', "Failure notice must have a stable test id");
  assertSourceIncludes(source, 'accessibilityLabel="打开相机扫码"', "Scan entry must have an accessible label");
  assertSourceIncludes(source, 'accessibilityRole="alert"', "Pairing failures must be announced as alerts");
  assert.doesNotMatch(source, /等待 HTTPS\/WSS 配对信息|需要启用 HTTPS\/WSS|手机 token|后端未启动|无法信任电脑证书/);
  assert.doesNotMatch(source, /不会打开相机|没有相机扫码组件|真机相机扫码仍未内置/);
}

function assertSmokeDoesNotClaimRealDeviceEvidence() {
  const source = fs.readFileSync(__filename, "utf8");
  const forbiddenClaims = [
    ["真", "实", "手", "机", "已", "验", "证"].join(""),
    ["真", "机", "已", "验", "证"].join(""),
    ["real", "phone", "verified"].join(" "),
    ["real", "device", "verified"].join(" "),
  ];
  for (const claim of forbiddenClaims) {
    assert.equal(source.includes(claim), false, `mobile token smoke must not claim ${claim} without real-device evidence`);
  }
}

function assertExpoCameraNativeConfig() {
  const appJson = JSON.parse(fs.readFileSync(mobilePath("app.json"), "utf8"));
  const expo = appJson.expo ?? {};
  const androidPermissions = expo.android?.permissions ?? [];
  assert.ok(Array.isArray(androidPermissions), "app.json expo.android.permissions must be an array");
  assert.ok(androidPermissions.includes("CAMERA"), "app.json must declare Android CAMERA permission for QR pairing");

  const iosCameraUsage = expo.ios?.infoPlist?.NSCameraUsageDescription;
  assert.equal(
    iosCameraUsage,
    "Allow Lengrvis Approval to use the camera to scan pairing QR codes from your desktop.",
    "app.json must explain iOS camera usage for QR pairing",
  );

  const plugins = expo.plugins ?? [];
  assert.ok(Array.isArray(plugins), "app.json expo.plugins must be an array");
  const cameraPlugin = plugins.find((plugin) => Array.isArray(plugin) && plugin[0] === "expo-camera");
  assert.ok(cameraPlugin, "app.json must configure the expo-camera config plugin");
  const cameraPluginConfig = cameraPlugin[1] ?? {};
  assert.equal(
    cameraPluginConfig.cameraPermission,
    iosCameraUsage,
    "expo-camera plugin cameraPermission should match the iOS camera usage explanation",
  );
  assert.equal(cameraPluginConfig.recordAudioAndroid, false, "QR pairing must not request Android audio recording permission");
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
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
  const client = loadMobileClient();
  const pairingPayload = loadPairingPayload(client);
  const desktopPairingPayload = loadDesktopPairingPayload();
  let expectedPairToken = "paired-token";
  assertPairScreenQrSourceAssertions();
  assertSmokeDoesNotClaimRealDeviceEvidence();
  assertExpoCameraNativeConfig();

  const server = await startHttpWsSmokeServer({
    handleRequest: ({ res, url, request }) => {
      if (request.method !== "POST" || url.pathname !== "/api/pair/confirm") return false;
      assertJsonRequest(request, {
        method: "POST",
        path: "/api/pair/confirm",
        body: { code: "abc123", device_name: "Phone" },
      });
      assert.match(String(request.headers.accept), /application\/json/);
      assert.match(String(request.headers["content-type"]), /application\/json/);
      jsonResponse(res, 200, {
        token: expectedPairToken,
        token_type: "Bearer",
        device_id: "device-1",
        expires_in: 3600,
        server: {
          host: "127.0.0.1",
          port: Number(url.port),
          protocol: "http",
          url: url.origin,
        },
        security: {
          transport: { http_scheme: "http", websocket_scheme: "ws", tls_enabled: false, advertised_base_url: url.origin },
          tls: { enabled: false, trust_status: "not_enabled" },
        },
      });
      return true;
    },
    handleUpgrade: ({ req, socket, url, upgrade }) => {
      const expectedProtocol = `lengrvis.mobile.token.${expectedPairToken}`;
      const knownPath = url.pathname === "/ws/mobile/approvals" || url.pathname === "/ws/remote/screen";
      if (knownPath && upgrade.protocols.includes(expectedProtocol)) {
        upgrade.accepted = true;
        acceptWebSocketUpgrade(req, socket, expectedProtocol);
        return;
      }
      rejectWebSocketUpgrade(socket, knownPath ? 401 : 404, "Bad mobile token protocol");
    },
  });

  try {
    const parsedJsonPayload = pairingPayload.parsePairingPayload(
      JSON.stringify({
        code: "ABC-123",
        server: { scheme: "https", host: "lengrvis.local", port: 8443 },
        expires_at: "2026-06-01T00:05:00.000Z",
      }),
    );
    assert.deepEqual(plain(parsedJsonPayload), {
      baseUrl: "https://lengrvis.local:8443",
      code: "abc123",
      expiresAt: "2026-06-01T00:05:00.000Z",
      source: "json",
    });
    const desktopGeneratedPayload = desktopPairingPayload.serializeMobilePairingPayload({
      code: "ZX-81-QP",
      expires_at: "2026-06-01T00:05:00.000Z",
      expires_in: 300,
      server: {
        host: "192.168.1.20",
        port: 8000,
        scheme: "http",
        transport_security: { http_scheme: "http", websocket_scheme: "ws", tls_enabled: false },
      },
      transport_security: { http_scheme: "http", websocket_scheme: "ws", tls_enabled: false },
      https_enabled: false,
      trust_required: false,
    });
    const desktopGeneratedJson = JSON.parse(desktopGeneratedPayload);
    assert.deepEqual(desktopGeneratedJson, {
      type: "lengrvis.mobile_pairing",
      version: 1,
      base_url: "http://192.168.1.20:8000",
      code: "ZX-81-QP",
      expires_at: "2026-06-01T00:05:00.000Z",
      expires_in: 300,
      server: {
        host: "192.168.1.20",
        port: 8000,
        scheme: "http",
        origin: "http://192.168.1.20:8000",
        transport_security: { http_scheme: "http", websocket_scheme: "ws", tls_enabled: false },
      },
      transport_security: { http_scheme: "http", websocket_scheme: "ws", tls_enabled: false },
      https_enabled: false,
      trust_required: false,
    });
    assert.deepEqual(plain(pairingPayload.parsePairingPayload(desktopGeneratedPayload)), {
      baseUrl: "http://192.168.1.20:8000",
      code: "zx81qp",
      expiresAt: "2026-06-01T00:05:00.000Z",
      source: "json",
    });
    assert.deepEqual(
      plain(pairingPayload.parsePairingPayload("lengrvis://pair?base_url=http%3A%2F%2F192.168.1.20%3A8000&code=def456")),
      {
        baseUrl: "http://192.168.1.20:8000",
        code: "def456",
        source: "url",
      },
    );
    assert.deepEqual(plain(pairingPayload.parsePairingPayload("电脑地址：http://192.168.1.20:8000 配对码：A1B2C3")), {
      baseUrl: "http://192.168.1.20:8000",
      code: "a1b2c3",
      source: "text",
    });
    const httpsPayloadState = pairingPayload.classifyPairingPayloadSecurity({
      baseUrl: "https://lengrvis.local:8443",
    });
    assert.equal(httpsPayloadState.status, "ready");
    assert.equal(httpsPayloadState.canPair, true);
    assert.equal(httpsPayloadState.security.webSocketProtocol, "wss:");

    const expiredPayloadState = pairingPayload.classifyPairingPayloadSecurity(
      {
        baseUrl: "https://lengrvis.local:8443",
        expiresAt: "2026-06-01T00:05:00.000Z",
      },
      Date.parse("2026-06-01T00:06:00.000Z"),
    );
    assert.equal(expiredPayloadState.status, "expired");
    assert.equal(expiredPayloadState.canPair, false);
    assert.equal(expiredPayloadState.security.kind, "https");

    const freshPayloadState = pairingPayload.classifyPairingPayloadSecurity(
      {
        baseUrl: "https://lengrvis.local:8443",
        expiresAt: "2026-06-01T00:05:00.000Z",
      },
      Date.parse("2026-06-01T00:04:00.000Z"),
    );
    assert.equal(freshPayloadState.status, "ready");
    assert.equal(freshPayloadState.canPair, true);

    const lanPayloadState = pairingPayload.classifyPairingPayloadSecurity({
      baseUrl: "http://192.168.1.20:8000",
    });
    assert.equal(lanPayloadState.status, "requires_https_wss");
    assert.equal(lanPayloadState.canPair, false);
    assert.notEqual(lanPayloadState.status, "ready");
    assert.equal(lanPayloadState.security.kind, "insecureLan");
    assert.match(lanPayloadState.security.warning, /HTTPS\/WSS/);

    const loopbackPayloadState = pairingPayload.classifyPairingPayloadSecurity({
      baseUrl: "http://127.0.0.1:8000",
    });
    assert.equal(loopbackPayloadState.status, "loopback");
    assert.equal(loopbackPayloadState.canPair, false);

    assert.throws(
      () => pairingPayload.parsePairingPayload("配对码：abc123"),
      (error) => error.name === "PairingPayloadParseError" && error.code === "missing_address",
    );
    assert.throws(
      () => pairingPayload.parsePairingPayload("电脑地址：http://192.168.1.20:8000"),
      (error) => error.name === "PairingPayloadParseError" && error.code === "missing_code",
    );

    assert.equal(client.normalizeBaseUrl("127.0.0.1:8000/"), "http://127.0.0.1:8000");
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
    assert.match(lanSecurity.warning, /HTTPS\/WSS/);
    assert.throws(() => client.assertSafeBaseUrl("http://192.168.1.20:8000"), (error) => {
      assert.equal(error.name, "InsecureLanBaseUrlError");
      assert.equal(error.security.kind, "insecureLan");
      return true;
    });

    const httpsSession = makeSession(client, "https://example.test:8443", "secure-token");
    const httpsApprovalInfo = client.approvalWebSocketConnectionInfo(httpsSession);
    assert.equal(httpsApprovalInfo.url, "wss://example.test:8443/ws/mobile/approvals");
    assert.equal(JSON.stringify(httpsApprovalInfo.protocols), JSON.stringify(["lengrvis.mobile.token.secure-token"]));
    assert.equal(httpsApprovalInfo.warning, undefined);

    const lanSession = makeSession(client, "http://192.168.1.20:8000", "lan-token");
    assert.throws(
      () => client.remoteScreenWebSocketConnectionInfo(lanSession),
      (error) => error.name === "InsecureLanBaseUrlError" && error.security.kind === "insecureLan",
    );
    assert.throws(
      () => client.remoteInputWebSocketConnectionInfo(lanSession, "input-token"),
      (error) => error.name === "InsecureLanBaseUrlError" && error.security.kind === "insecureLan",
    );
    await assert.rejects(
      () => client.listPendingApprovals({ ...lanSession, baseUrlSecurity: httpsSession.baseUrlSecurity }),
      (error) => error.name === "InsecureLanBaseUrlError" && error.security.kind === "insecureLan",
    );

    await assert.rejects(
      () => client.pairWithBackend("http://192.168.1.20:8000", "abc123", "Phone"),
      (error) => error.name === "InsecureLanBaseUrlError" && error.security.kind === "insecureLan",
    );
    await assert.rejects(
      () => client.pairWithBackend("http://192.168.1.20:8000", "abc123", "Phone", { allowInsecureLan: true }),
      (error) => error.name === "InsecureLanBaseUrlError" && error.security.kind === "insecureLan",
    );

    const insecureStorage = makeStorage();
    const insecureAuth = loadAuth(client, insecureStorage);
    insecureStorage.asyncMap.set(
      "lengrvis.mobile.session",
      JSON.stringify({
        baseUrl: "http://192.168.1.20:8000",
        baseUrlSecurity: lanSession.baseUrlSecurity,
        deviceId: lanSession.deviceId,
      }),
    );
    insecureStorage.secureMap.set("lengrvis.mobile.session.token", "old-lan-token");
    assert.equal(await insecureAuth.loadSession(), null);
    assert.equal(insecureStorage.asyncMap.has("lengrvis.mobile.session"), false);
    assert.equal(insecureStorage.secureMap.has("lengrvis.mobile.session.token"), false);
    await assert.rejects(
      () => insecureAuth.saveSession(lanSession),
      (error) => error.name === "InsecureLanBaseUrlError" && error.security.kind === "insecureLan",
    );
    assert.equal(server.requests.length, 0, "blocked insecure LAN pair attempts must not reach the smoke server");

    const paired = await client.pairWithBackend(`${server.origin}/`, "abc123", "Phone");
    assert.equal(server.requests.length, 1, "pairing must reach the local HTTP smoke service");
    assert.equal(paired.baseUrl, server.origin);
    assert.equal(paired.token, expectedPairToken);
    assert.equal(paired.deviceId, "device-1");
    assert.equal(paired.baseUrlSecurity.kind, "loopbackHttp");
    assert.equal(paired.server.port, Number(new URL(server.origin).port));
    assert.equal(paired.security.transport.httpScheme, "http");
    assert.equal(paired.security.transport.webSocketScheme, "ws");
    assert.equal(paired.security.tls.trustStatus, "not_enabled");

    const approvalInfo = client.approvalWebSocketConnectionInfo(paired);
    assert.equal(approvalInfo.url, `${server.origin.replace("http:", "ws:")}/ws/mobile/approvals`);
    assert.equal(JSON.stringify(approvalInfo.protocols), JSON.stringify([`lengrvis.mobile.token.${expectedPairToken}`]));
    assert.doesNotMatch(approvalInfo.url, /paired-token|token=/);
    assertAcceptedWebSocket(await connectWebSocket(approvalInfo.url, approvalInfo.protocols), approvalInfo.protocols[0]);

    const screenInfo = client.remoteScreenWebSocketConnectionInfo(paired);
    assert.equal(screenInfo.url, `${server.origin.replace("http:", "ws:")}/ws/remote/screen`);
    assert.equal(JSON.stringify(screenInfo.protocols), JSON.stringify([`lengrvis.mobile.token.${expectedPairToken}`]));
    assert.doesNotMatch(screenInfo.url, /paired-token|token=/);
    assertAcceptedWebSocket(await connectWebSocket(screenInfo.url, screenInfo.protocols), screenInfo.protocols[0]);

    const rejectedScreen = await connectWebSocket(screenInfo.url, ["lengrvis.mobile.token.wrong"]);
    assert.equal(rejectedScreen.statusCode, 401);
    assert.equal(server.upgrades.length, 3);
    assert.equal(server.upgrades.filter((upgrade) => upgrade.accepted).length, 2);

    const tlsTrustSecurity = client.describeBaseUrlSecurity("https://example.test:8443", {
      transport: { http_scheme: "https", websocket_scheme: "wss", tls_enabled: true },
      tls: {
        enabled: true,
        trust_status: "requires_trust",
        requires_trust: true,
        self_signed: true,
        fingerprint_sha256: "aabbccddeeff00112233445566778899",
      },
    });
    assert.equal(tlsTrustSecurity.webSocketProtocol, "wss:");
    assert.equal(tlsTrustSecurity.requiresTlsTrust, true);
    assert.equal(tlsTrustSecurity.serverTls.fingerprintSha256, "AABBCCDDEEFF00112233445566778899");
    assert.equal(client.formatTlsFingerprint(tlsTrustSecurity.serverTls.fingerprintSha256), "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99");

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
    assert.equal(migrated.baseUrl, server.origin);
    assert.equal(migratedStorage.secureMap.get("lengrvis.mobile.session.token"), "legacy-token");
    assert.doesNotMatch(migratedStorage.asyncMap.get("lengrvis.mobile.session"), /legacy-token/);

    expectedPairToken = "stored-token";
    const storedSession = await client.pairWithBackend(`${server.origin}/`, "abc123", "Phone");
    await migratedAuth.saveSession(storedSession);
    const storedMetadata = JSON.parse(migratedStorage.asyncMap.get("lengrvis.mobile.session"));
    assert.equal(storedMetadata.baseUrl, server.origin);
    assert.equal(migratedStorage.secureMap.get("lengrvis.mobile.session.token"), "stored-token");
    assert.doesNotMatch(migratedStorage.asyncMap.get("lengrvis.mobile.session"), /stored-token/);
  } finally {
    await server.close();
  }
}

main()
  .then(() => console.log("Mobile token behavior smoke passed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
