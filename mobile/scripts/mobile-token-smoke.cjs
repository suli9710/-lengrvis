const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  acceptWebSocketUpgrade,
  assertAcceptedWebSocket,
  assertInsecureLanError,
  assertJsonRequest,
  assertWebSocketTokenTransport,
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
      if (id === "../api/client/nativeTlsTrust") {
        return loadTsModule(mobilePath("src/api/client/nativeTlsTrust.ts"));
      }
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

function readPairScreenHelperSource() {
  const screensDir = mobilePath("src/screens");
  return fs
    .readdirSync(screensDir)
    .filter((name) => /^pairScreen(?!Styles).*\.ts$/.test(name))
    .sort()
    .map((name) => fs.readFileSync(path.join(screensDir, name), "utf8"))
    .join("\n");
}

function assertPairScreenQrSourceAssertions() {
  const source = fs.readFileSync(mobilePath("src/screens/PairScreen.tsx"), "utf8");
  const pairScreenSources = `${source}\n${readPairScreenHelperSource()}`;
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
  assertSourceIncludes(source, "const activeDetectedPayload = detectedPayload;", "PairScreen must keep parsed QR payload metadata active across manual-entry toggles");
  assertSourceIncludes(
    source,
    "const isDetectedPayloadBlocked = Boolean(detectedPayloadSecurity && !detectedPayloadSecurity.canPair);",
    "PairScreen must not let manual entry bypass unsafe parsed QR metadata",
  );
  assertSourceIncludes(
    source,
    "baseUrlSecurityHint(baseUrl, activeDetectedPayload?.security)",
    "Manual-entry security hint must still honor parsed QR transport metadata",
  );
  assertSourceIncludes(
    source,
    "describeBaseUrlSecurity(nextBaseUrl, nextPayload?.security)",
    "Pair submit must fail closed using parsed QR transport metadata before sending a pairing request",
  );
  assertSourceIncludes(source, "const pairRequestLockedRef = useRef(false);", "Pair submit must use a synchronous lock in addition to React busy state");
  assertSourceMatches(
    source,
    /if \(isBusy \|\| pairRequestLockedRef\.current\) return;[\s\S]*pairRequestLockedRef\.current = true;[\s\S]*finally \{[\s\S]*pairRequestLockedRef\.current = false;[\s\S]*setIsBusy\(false\);[\s\S]*\n    \}/,
    "Pair submit must release the synchronous busy lock on every validation or network path",
  );
  assertSourceIncludes(
    source,
    "const nextPayloadSecurity = nextPayload ? classifyPairingPayloadSecurity(nextPayload) : null;",
    "Pair submit must re-check parsed QR metadata inside the handler",
  );
  assertSourceIncludes(
    source,
    "blockedPairingPayloadFailureNotice(nextPayloadSecurity.status)",
    "Pair submit must surface beginner-readable failures when QR metadata blocks pairing",
  );
  assertSourceMatches(
    source,
    /const handleManualBaseUrlChange = \(value: string\) => \{[\s\S]*const protectedInput = protectBaseUrlInput\(value\);[\s\S]*setBaseUrl\(protectedInput\.value\);[\s\S]*setDetectedPayload\(null\);[\s\S]*setPairingPayload\(""\);[\s\S]*setFailure\(protectedInput\.notice\);[\s\S]*\};/,
    "Manual computer address edits must clear parsed QR metadata after sanitizing the address",
  );
  assertSourceIncludes(
    source,
    "onChangeText={handleManualBaseUrlChange}",
    "Manual computer address input must use the shared sanitizer handler",
  );
  assertSourceIncludes(
    pairScreenSources,
    'return value.replace(/[^a-z0-9]/gi, "").toLowerCase().slice(0, MAX_PAIRING_CODE_LENGTH);',
    "Manual pairing code input must be normalized and capped to the 16-character backend contract",
  );
  assert.doesNotMatch(
    source,
    /Boolean\(!showManualEntry && detectedPayloadSecurity && !detectedPayloadSecurity\.canPair\)/,
    "Manual-entry toggle must not disable unsafe parsed QR blocking",
  );

  assertSourceMatches(
    source,
    /const handleBarcodeScanned = \(result: BarcodeScanningResult\) => \{[\s\S]*const protectedInput = protectPairingPayloadInput\(result\.data\);[\s\S]*const payload = parsePairingPayload\(protectedInput\.value\);[\s\S]*setPairingPayload\(protectedInput\.value\);[\s\S]*applyPayload\(payload\);[\s\S]*closeScanner\(pairingFailureNotice\(currentError, undefined, "scan"\)\);[\s\S]*\n  \};/,
    "Barcode scan handler must sanitize scanned QR payload data, apply parsed payloads, and close through the scanner recovery path",
  );
  assertSourceMatches(
    source,
    /const handleBarcodeScanned = \(result: BarcodeScanningResult\) => \{[\s\S]*\} catch \(currentError\) \{[\s\S]*setPairingPayload\(""\);[\s\S]*setDetectedPayload\(null\);[\s\S]*closeScanner\(pairingFailureNotice\(currentError, undefined, "scan"\)\);[\s\S]*\n    \}[\s\S]*\n  \};/,
    "Failed scans must clear stale scanned payload before surfacing recovery copy",
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
    "位配对码",
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
    assertSourceIncludes(pairScreenSources, copy, `PairScreen beginner copy must explain ${copy}`);
  }

  assertSourceIncludes(source, 'testID="pair-open-scanner-button"', "Scan entry must have a stable test id");
  assertSourceIncludes(source, 'testID="pairing-scanner-camera"', "Camera view must have a stable test id");
  assertSourceIncludes(source, 'testID="pair-failure-notice"', "Failure notice must have a stable test id");
  assertSourceIncludes(source, 'accessibilityLabel="打开相机扫码"', "Scan entry must have an accessible label");
  assertSourceIncludes(source, 'accessibilityRole="alert"', "Pairing failures must be announced as alerts");
  assertSourceIncludes(source, 'accessibilityValue={{ text: payloadAccessibilityValue }}', "QR payload input must expose length to Android accessibility services");
  assertSourceIncludes(source, 'accessibilityValue={{ text: baseUrlAccessibilityValue }}', "Manual address input must expose length to Android accessibility services");
  assertSourceIncludes(source, 'accessibilityValue={{ text: pairCodeAccessibilityValue }}', "Manual pairing code input must expose normalized length to Android accessibility services");
  assert.ok((source.match(/importantForAutofill="no"/g) ?? []).length >= 3, "Pairing inputs must opt out of Android autofill");
  assert.ok((source.match(/autoComplete="off"/g) ?? []).length >= 3, "Pairing inputs must disable autocomplete suggestions");
  assertSourceIncludes(
    source,
    'accessibilityRole={manualBaseUrlNotice.tone === "danger" ? "alert" : undefined}',
    "Dangerous manual address security hints must be announced on Android",
  );
  assertSourceIncludes(source, "protectPairingPayloadInput(result.data)", "Scanned QR payloads must share length and control-character protection with pasted payloads");
  assertSourceIncludes(
    pairScreenSources,
    'const withoutUnsafeCharacters = value.replace(/[\\u0000-\\u001f\\u007f]+/g, " ");',
    "Pairing payload input must replace C0/DEL controls with spaces before parsing or echoing",
  );
  assertSourceIncludes(
    pairScreenSources,
    "notice: withoutUnsafeCharacters.length > MAX_BASE_URL_LENGTH ? pairingInputTooLongNotice(\"baseUrl\") : baseUrlInputCleanedNotice(),",
    "Manual address cleanup must only show the length warning when the sanitized address exceeds the limit",
  );
  assert.doesNotMatch(pairScreenSources, /等待 HTTPS\/WSS 配对信息|需要启用 HTTPS\/WSS|手机 token|后端未启动|无法信任电脑证书/);
  assert.doesNotMatch(pairScreenSources, /不会打开相机|没有相机扫码组件|真机相机扫码仍未内置/);
}

function assertAppShellSourceAssertions() {
  const source = fs.readFileSync(mobilePath("app/_layout.tsx"), "utf8");
  assertSourceIncludes(source, 'type SessionLoadState = "loading" | "ready" | "failed";', "App must model stored-session loading explicitly");
  assertSourceIncludes(source, 'testID="app-session-load-screen"', "App loading/recovery screen must have a stable test id");
  assertSourceIncludes(source, "正在安全读取或清理这台手机保存的配对状态", "App must describe stored-session loading and cleanup as safe local recovery");
  assertSourceIncludes(source, "手机没有读到可用的本地会话", "App must explain failed stored-session recovery without raw storage errors");
  assert.doesNotMatch(source, /AsyncStorage|SecureStore|Error:/, "App session recovery UX must not expose storage internals or raw errors");
  assertSourceMatches(
    source,
    /const resetShellState = useCallback\(\(\) => \{[\s\S]*clearRemoteInputGrantTokens\(\);[\s\S]*setRemoteInputGrant\(\(current\) => reduceRemoteInputGrant\(current, \{ type: "cleared" \}\)\);[\s\S]*router\.replace\("\/home"\);[\s\S]*\}, \[router\]\);/,
    "App shell reset must clear remote input grants and return to the home tab",
  );
  assert.doesNotMatch(source, /setSelectedApproval|setActiveScreen/, "Expo Router shell must not keep legacy selected-approval or active-screen state");
  assertSourceMatches(
    source,
    /const clearLocalSessionOrShowRecovery = useCallback\(\(\) => \{[\s\S]*resetShellState\(\);[\s\S]*setSession\(null\);[\s\S]*setSessionLoadState\("loading"\);[\s\S]*void clearSession\(\)[\s\S]*\.then\(\(\) => \{[\s\S]*setSessionLoadState\("ready"\);[\s\S]*router\.replace\("\/"\);[\s\S]*\}\)[\s\S]*\.catch\(\(\) => \{[\s\S]*setSessionLoadState\("failed"\);[\s\S]*router\.replace\("\/"\);[\s\S]*\}\);[\s\S]*\}, \[resetShellState, router\]\);/,
    "Stored session cleanup must drop in-memory approval/session/grant state before async storage clearing and fail closed into recovery",
  );
  assertSourceIncludes(source, "const handlePaired = useCallback((nextSession: PairingSession) => {", "Pairing completion must reset shell state through one handler");
  assertSourceMatches(
    source,
    /const handlePaired = useCallback\(\(nextSession: PairingSession\) => \{[\s\S]*resetShellState\(\);[\s\S]*setSessionLoadState\("ready"\);[\s\S]*setSession\(nextSession\);[\s\S]*router\.replace\("\/home"\);[\s\S]*\}, \[resetShellState, router\]\);/,
    "Pairing completion must not preserve old approval detail or remote input grants",
  );
  assertSourceIncludes(source, "onPairFresh={clearLocalSessionOrShowRecovery}", "Fresh pairing from recovery must clear stored credentials through the recovery-safe cleanup path");
  assert.doesNotMatch(
    source,
    /clearSession\(\)\.catch\(\(\) => undefined\)/,
    "App must not claim local session cleanup succeeded when storage clearing fails",
  );
  assertSourceMatches(
    source,
    /<MobileCompanionProvider[\s\S]*onSelectApproval=\{\(approval\) => router\.push\(\{ pathname: "\/approval\/\[id\]", params: \{ id: approval\.id \} \}\)\}[\s\S]*onSessionExpired=\{clearLocalSessionOrShowRecovery\}[\s\S]*session=\{session\}/,
    "Companion routes must send approval navigation and auth expiry through the root recovery shell",
  );
  const approvalsSource = fs.readFileSync(mobilePath("src/screens/ApprovalsScreen.tsx"), "utf8");
  assert.doesNotMatch(
    approvalsSource,
    /from "\.\.\/store\/auth"|clearSession\(\)|finally\(onUnpair\)/,
    "ApprovalsScreen must not bypass App session recovery when clearing local credentials",
  );
  assertSourceIncludes(source, "onSessionExpired={clearLocalSessionOrShowRecovery}", "Remote and companion screens must be able to clear an expired mobile session");
  assertSourceIncludes(source, 'accessibilityRole={isLoading ? "progressbar" : "alert"}', "Stored-session recovery must expose loading and failed states to Android accessibility");
  assertSourceIncludes(source, 'accessibilityHint="清理本地会话并回到配对页面"', "Fresh pairing recovery action must explain that it clears local session state");
  assertSourceIncludes(source, "resetShellState();", "Unpair must clear selected approval and remote input grant shell state through the shared recovery path");
  assertSourceMatches(
    source,
    /let isActive = true;[\s\S]*getApprovalDetail\(session, approvalId\)[\s\S]*if \(!isActive\) return;[\s\S]*router\.push\(\{ pathname: "\/approval\/\[id\]", params: \{ id: detail\.approval\.id \} \}\);[\s\S]*return \(\) => \{[\s\S]*isActive = false;[\s\S]*subscription\.remove\(\);[\s\S]*\};/,
    "Notification-opened approval detail loads must not rehydrate stale selections after unpair and must leave remote screen to show the detail",
  );
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

async function assertNativeTlsTrustRuntimeBoundaries(client) {
  const source = fs.readFileSync(mobilePath("src/api/client/nativeTlsTrust.ts"), "utf8");
  assertSourceIncludes(
    source,
    "iOS LAN certificate pinning is not available yet",
    "iOS must fail closed when local LAN certificate pinning would be required",
  );
  assertSourceIncludes(
    source,
    "This mobile runtime cannot configure LAN certificate pinning for local HTTPS pairing.",
    "Non-Android runtimes must fail closed when local LAN certificate pinning would be required",
  );
  assert.doesNotMatch(
    source,
    /attestation_verified:\s*true|hardware_attestation|hardware attested/i,
    "native TLS trust source must not claim hardware device attestation",
  );

  const pinnedSecurity = client.describeBaseUrlSecurity("https://example.test:8443", {
    transport: { http_scheme: "https", websocket_scheme: "wss", tls_enabled: true },
    tls: {
      enabled: true,
      trust_status: "requires_trust",
      requires_trust: true,
      self_signed: true,
      fingerprint_sha256: "aa:bb:cc:dd",
    },
  });
  const calls = [];
  const androidTrust = loadTsModule(mobilePath("src/api/client/nativeTlsTrust.ts"), {
    require: (id) => {
      if (id === "react-native") {
        return {
          Platform: { OS: "android" },
          NativeModules: {
            LengrvisLanTrust: {
              trustServerCertificate: async (baseUrl, fingerprint) => calls.push({ baseUrl, fingerprint }),
              clearTrustedServers: async () => calls.push({ clear: true }),
            },
          },
        };
      }
      return require(id);
    },
  });
  await androidTrust.configureNativeTlsTrust(pinnedSecurity);
  assert.deepEqual(calls, [{ baseUrl: "https://example.test:8443", fingerprint: "AA:BB:CC:DD" }]);
  await androidTrust.clearNativeTlsTrust();
  assert.deepEqual(calls[1], { clear: true });

  for (const osName of ["ios", "web"]) {
    const trust = loadTsModule(mobilePath("src/api/client/nativeTlsTrust.ts"), {
      require: (id) => {
        if (id === "react-native") return { Platform: { OS: osName }, NativeModules: {} };
        return require(id);
      },
    });
    await assert.rejects(
      () => trust.configureNativeTlsTrust(pinnedSecurity),
      (error) => error?.name === "TlsTrustConfigurationError" && /pinning|runtime/.test(String(error.message)),
    );
  }
}

function assertExpoCameraNativeConfig() {
  const appJson = JSON.parse(fs.readFileSync(mobilePath("app.json"), "utf8"));
  const expo = appJson.expo ?? {};
  const androidPermissions = expo.android?.permissions ?? [];
  assert.ok(Array.isArray(androidPermissions), "app.json expo.android.permissions must be an array");
  assert.ok(androidPermissions.includes("CAMERA"), "app.json must declare Android CAMERA permission for QR pairing");
  const blockedPermissions = expo.android?.blockedPermissions ?? [];
  for (const permission of [
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.SYSTEM_ALERT_WINDOW",
    "android.permission.WRITE_EXTERNAL_STORAGE",
  ]) {
    assert.ok(blockedPermissions.includes(permission), `app.json must block unused Android permission ${permission}`);
  }
  assert.equal(
    expo.android?.usesCleartextTraffic,
    false,
    "Android builds must not permit app-wide cleartext traffic; mobile LAN HTTP is blocked by the app contract",
  );

  const iosCameraUsage = expo.ios?.infoPlist?.NSCameraUsageDescription;
  assert.equal(
    iosCameraUsage,
    "允许 Lengrvis 手机端使用相机扫描电脑端配对二维码。",
    "app.json must explain iOS camera usage for QR pairing",
  );

const plugins = expo.plugins ?? [];
  assert.ok(Array.isArray(plugins), "app.json expo.plugins must be an array");
  assert.ok(
    plugins.includes("./plugins/withAndroidRemoteControlHardening"),
    "app.json must include Android remote-control hardening plugin for LAN TLS trust and FLAG_SECURE",
  );
  const hardeningPluginSource = fs.readFileSync(mobilePath("plugins", "withAndroidRemoteControlHardening.js"), "utf8");
  for (const requiredFragment of [
    "network_security_config",
    'certificates src="system"',
    'cleartextTrafficPermitted="false"',
    "AndroidConfig.Permissions.removePermissions",
    "android:networkSecurityConfig",
    "android:usesCleartextTraffic",
    "WindowManager.LayoutParams.FLAG_SECURE",
  ]) {
    assert.ok(
      hardeningPluginSource.includes(requiredFragment),
      `Android hardening plugin must include ${requiredFragment}`,
    );
  }
  assert.equal(
    /<certificates\s+src="user"/.test(hardeningPluginSource),
    false,
    "Android hardening must not trust user-installed CAs by default",
  );

  const gradleSource = fs.readFileSync(mobilePath("android", "app", "build.gradle"), "utf8");
  for (const requiredFragment of [
    "releaseSigningConfigured",
    "releaseTaskRequested",
    "throw new GradleException",
    "signingConfig signingConfigs.release",
  ]) {
    assert.ok(
      gradleSource.includes(requiredFragment),
      `Android release signing must include ${requiredFragment}`,
    );
  }
  assert.equal(
    gradleSource.includes("release.keystore"),
    false,
    "Android release signing must not fall back to a placeholder keystore",
  );

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
  const failures = {
    removeItem: null,
    deleteItemAsync: null,
  };
  return {
    asyncMap,
    secureMap,
    failures,
    asyncStorage: {
      getItem: async (key) => asyncMap.get(key) ?? null,
      setItem: async (key, value) => {
        asyncMap.set(key, String(value));
      },
      removeItem: async (key) => {
        if (failures.removeItem) throw failures.removeItem;
        asyncMap.delete(key);
      },
    },
    secureStore: {
      getItemAsync: async (key) => secureMap.get(key) ?? null,
      setItemAsync: async (key, value) => {
        secureMap.set(key, String(value));
      },
      deleteItemAsync: async (key) => {
        if (failures.deleteItemAsync) throw failures.deleteItemAsync;
        secureMap.delete(key);
      },
    },
  };
}

function assertSessionRecoveryError(error) {
  assert.equal(error?.name, "SessionRecoveryError");
  assert.match(String(error?.message ?? ""), /本地会话/);
  assert.doesNotMatch(String(error?.message ?? ""), /AsyncStorage|SecureStore|token|secret|raw/i);
  return true;
}

async function main() {
  const client = loadMobileClient();
  const pairingPayload = loadPairingPayload(client);
  const desktopPairingPayload = loadDesktopPairingPayload();
  let expectedPairToken = "paired-token";
  let pairResponseOverrides = {};
  assertAppShellSourceAssertions();
  assertPairScreenQrSourceAssertions();
  assertSmokeDoesNotClaimRealDeviceEvidence();
  await assertNativeTlsTrustRuntimeBoundaries(client);
  assertExpoCameraNativeConfig();

  const server = await startHttpWsSmokeServer({
    handleRequest: ({ res, url, request }) => {
      assert.equal(url.search, "", "mobile pair confirm requests must not inherit pasted base URL query strings");
      assert.doesNotMatch(request.url, /[?&](?:token|access_token|auth|authorization)=/i, "mobile pair confirm requests must not carry query auth");
      if (request.method !== "POST" || url.pathname !== "/api/pair/confirm") return false;
      assertJsonRequest(request, {
        method: "POST",
        path: "/api/pair/confirm",
        body: { code: "abcd1234ef567890", device_name: "Phone", claim_secret: "claim-secret-for-mobile-smoke-123456" },
      });
      assert.match(String(request.headers.accept), /application\/json/);
      assert.match(String(request.headers["content-type"]), /application\/json/);
      const pairResponse = {
        token: expectedPairToken,
        token_type: "Bearer",
        device_id: "device-1",
        device_trust: {
          attestation_verified: false,
          attestation_status: "not_verified",
          attestation_provider: "none",
          trust_basis: "pairing_code_tls",
          hardware_backed: false,
        },
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
        ...pairResponseOverrides,
      };
      pairResponseOverrides = {};
      jsonResponse(res, 200, pairResponse);
      return true;
    },
    handleUpgrade: ({ req, socket, url, upgrade }) => {
      const expectedProtocol = `lengrvis.mobile.token.${expectedPairToken}`;
      const knownPath = url.pathname === "/ws/mobile/approvals" || url.pathname === "/ws/remote/screen";
      if (knownPath) assert.equal(url.search, "", "mobile WebSocket upgrades must not carry query auth");
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
        code: "ABCD-1234-EF56-7890",
        claim_secret: "claim-secret-from-json-payload-123456",
        server: { scheme: "https", host: "lengrvis.local", port: 8443 },
        expires_at: "2026-06-01T00:05:00.000Z",
      }),
    );
    assert.deepEqual(plain(parsedJsonPayload), {
      baseUrl: "https://lengrvis.local:8443",
      code: "abcd1234ef567890",
      expiresAt: "2026-06-01T00:05:00.000Z",
      source: "json",
    });
    assert.equal(parsedJsonPayload.claimSecret, "claim-secret-from-json-payload-123456");
    const desktopGeneratedPayload = desktopPairingPayload.serializeMobilePairingPayload({
      code: "ZX81-QP12-LM34-RT56",
      claim_secret: "claim-secret-from-desktop-payload-123456",
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
      code: "ZX81-QP12-LM34-RT56",
      claim_secret: "claim-secret-from-desktop-payload-123456",
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
      code: "zx81qp12lm34rt56",
      expiresAt: "2026-06-01T00:05:00.000Z",
      source: "json",
    });
    assert.equal(pairingPayload.parsePairingPayload(desktopGeneratedPayload).claimSecret, "claim-secret-from-desktop-payload-123456");
    const urlPayload = pairingPayload.parsePairingPayload(
      "lengrvis://pair?base_url=http%3A%2F%2F192.168.1.20%3A8000&code=def45678abc90123&claim_secret=claim-secret-from-url-payload-123456",
    );
    assert.deepEqual(
      plain(urlPayload),
      {
        baseUrl: "http://192.168.1.20:8000",
        code: "def45678abc90123",
        source: "url",
      },
    );
    assert.equal(urlPayload.claimSecret, "claim-secret-from-url-payload-123456");
    const queryBearingQrPayload = pairingPayload.parsePairingPayload(
      `lengrvis://pair?base_url=${encodeURIComponent("https://mobile-token:secret@example.test:8443/copied/path?token=secret-token#pair")}&code=ABCD1234EF567890&tls_enabled=true&websocket_scheme=wss`,
    );
    assert.deepEqual(plain(queryBearingQrPayload), {
      baseUrl: "https://example.test:8443",
      code: "abcd1234ef567890",
      source: "url",
    });
    assert.doesNotMatch(queryBearingQrPayload.baseUrl, /mobile-token|secret-token|[?&]token=/);

    const metadataBlockedQrPayload = pairingPayload.parsePairingPayload(
      JSON.stringify({
        base_url: "https://mobile-token:secret@example.test:8443/copied/path?access_token=secret-token#pair",
        code: "ABCD1234EF567890",
        transport_security: { http_scheme: "https", websocket_scheme: "ws", tls_enabled: false },
      }),
    );
    assert.equal(metadataBlockedQrPayload.baseUrl, "https://example.test:8443");
    assert.doesNotMatch(metadataBlockedQrPayload.baseUrl, /mobile-token|secret-token|access_token/);
    assert.equal(metadataBlockedQrPayload.security.transport.webSocketScheme, "ws");
    const metadataBlockedQrPayloadState = pairingPayload.classifyPairingPayloadSecurity(metadataBlockedQrPayload);
    assert.equal(metadataBlockedQrPayloadState.status, "requires_https_wss");
    assert.equal(metadataBlockedQrPayloadState.canPair, false);

    assert.deepEqual(plain(pairingPayload.parsePairingPayload("电脑地址：http://192.168.1.20:8000 配对码：A1B2C3D4E5F60718")), {
      baseUrl: "http://192.168.1.20:8000",
      code: "a1b2c3d4e5f60718",
      source: "text",
    });
    assert.deepEqual(plain(pairingPayload.parsePairingPayload("\u0000电脑地址：https://example.test:8443/copied/path?token=secret-token\r\n配对码：A1B2C3D4E5F60718\u007f")), {
      baseUrl: "https://example.test:8443",
      code: "a1b2c3d4e5f60718",
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

    const invalidExpiryPayloadState = pairingPayload.classifyPairingPayloadSecurity({
      baseUrl: "https://lengrvis.local:8443",
      expiresAt: "not-a-date",
    });
    assert.equal(invalidExpiryPayloadState.status, "expired");
    assert.equal(invalidExpiryPayloadState.canPair, false);

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
      () => pairingPayload.parsePairingPayload("配对码：abcd1234ef567890"),
      (error) => error.name === "PairingPayloadParseError" && error.code === "missing_address",
    );
    assert.throws(
      () => pairingPayload.parsePairingPayload("电脑地址：http://192.168.1.20:8000"),
      (error) => error.name === "PairingPayloadParseError" && error.code === "missing_code",
    );

    assert.equal(client.normalizeBaseUrl("127.0.0.1:8000/"), "http://127.0.0.1:8000");
    assert.equal(client.normalizeBaseUrl("https://Example.test:8443/"), "https://example.test:8443");
    assert.equal(client.normalizeBaseUrl("https://mobile-token:secret@example.test:8443/copied/path?token=secret#pair"), "https://example.test:8443");
    assert.equal(client.normalizeBaseUrl("127.0.0.1:8000/copied/path?access_token=secret#pair"), "http://127.0.0.1:8000");
    assert.throws(() => client.normalizeBaseUrl("ftp://example.test"), /http:\/\/.*https:\/\//);

    const httpsSecurity = client.describeBaseUrlSecurity("https://example.test:8443/");
    assert.equal(httpsSecurity.kind, "https");
    assert.equal(httpsSecurity.isHttps, true);
    assert.equal(httpsSecurity.isInsecureLan, false);

    const tlsDisabledSecurity = client.describeBaseUrlSecurity("https://example.test:8443", {
      transport: { http_scheme: "https", websocket_scheme: "ws", tls_enabled: false },
    });
    assert.equal(tlsDisabledSecurity.kind, "insecureLan");
    assert.equal(tlsDisabledSecurity.isInsecureLan, true);
    assert.equal(tlsDisabledSecurity.backendTlsEnabled, false);
    assert.equal(tlsDisabledSecurity.webSocketProtocol, "ws:");
    assert.match(tlsDisabledSecurity.warning, /未启用 TLS|HTTPS/);
    const tlsDisabledSession = {
      ...makeSession(client, "https://example.test:8443", "secure-token"),
      baseUrlSecurity: tlsDisabledSecurity,
    };
    assert.throws(
      () => client.remoteScreenWebSocketConnectionInfo(tlsDisabledSession),
      assertInsecureLanError,
    );
    assert.throws(
      () => client.remoteInputWebSocketConnectionInfo(tlsDisabledSession, "input-token"),
      assertInsecureLanError,
    );

    const loopbackSecurity = client.describeBaseUrlSecurity("http://127.0.0.1:8000");
    assert.equal(loopbackSecurity.kind, "loopbackHttp");
    assert.equal(loopbackSecurity.isLoopback, true);
    assert.equal(loopbackSecurity.requiresExplicitAllow, false);
    assert.equal(client.isLoopbackBaseUrl("http://[::1]:8000"), true);

    const loopbackStorage = makeStorage();
    const loopbackAuth = loadAuth(client, loopbackStorage);
    loopbackStorage.asyncMap.set(
      "lengrvis.mobile.session",
      JSON.stringify({
        baseUrl: "http://127.0.0.1:8000",
        baseUrlSecurity: loopbackSecurity,
        deviceId: "device-1",
      }),
    );
    loopbackStorage.secureMap.set("lengrvis.mobile.session.token", "old-loopback-token");
    assert.equal(
      await loopbackAuth.loadSession(),
      null,
      "stored loopback sessions must be cleared and must not restore as paired",
    );
    assert.equal(loopbackStorage.asyncMap.has("lengrvis.mobile.session"), false);
    assert.equal(loopbackStorage.secureMap.has("lengrvis.mobile.session.token"), false);

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
    assertWebSocketTokenTransport(httpsApprovalInfo, "secure-token", { pathname: "/ws/mobile/approvals", label: "secure approval WebSocket" });
    assert.equal(httpsApprovalInfo.warning, undefined);
    assert.deepEqual(client.mobileTokenWebSocketProtocols("token.with-allowed_chars~"), ["lengrvis.mobile.token.token.with-allowed_chars~"]);
    assert.throws(
      () => client.mobileTokenWebSocketProtocols("bad token"),
      (error) => error.name === "ForbiddenError" && /WebSocket/.test(error.message),
    );
    assert.throws(
      () => client.assertSafePairingSession({ ...httpsSession, token: "bad token" }),
      (error) => error.name === "ForbiddenError" && /WebSocket/.test(error.message),
    );
    assert.throws(
      () => client.assertSafePairingSession({ ...httpsSession, expiresAt: new Date(Date.now() - 1000).toISOString() }),
      (error) => error.name === "AuthExpiredError",
    );

    const lanSession = makeSession(client, "http://192.168.1.20:8000", "lan-token");
    assert.throws(
      () => client.remoteScreenWebSocketConnectionInfo(lanSession),
      assertInsecureLanError,
    );
    assert.throws(
      () => client.remoteInputWebSocketConnectionInfo(lanSession, "input-token"),
      assertInsecureLanError,
    );
    await assert.rejects(
      () => client.listPendingApprovals({ ...lanSession, baseUrlSecurity: httpsSession.baseUrlSecurity }),
      assertInsecureLanError,
    );

    await assert.rejects(
      () => client.pairWithBackend("http://192.168.1.20:8000", "abcd1234ef567890", "Phone"),
      assertInsecureLanError,
    );
    await assert.rejects(
      () => client.pairWithBackend("http://192.168.1.20:8000", "abcd1234ef567890", "Phone", { allowInsecureLan: true }),
      assertInsecureLanError,
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
      assertInsecureLanError,
    );
    assert.equal(server.requests.length, 0, "blocked insecure LAN pair attempts must not reach the smoke server");

    const unclearedInsecureStorage = makeStorage();
    const unclearedInsecureAuth = loadAuth(client, unclearedInsecureStorage);
    unclearedInsecureStorage.asyncMap.set(
      "lengrvis.mobile.session",
      JSON.stringify({
        baseUrl: "http://192.168.1.20:8000",
        baseUrlSecurity: lanSession.baseUrlSecurity,
        deviceId: lanSession.deviceId,
      }),
    );
    unclearedInsecureStorage.secureMap.set("lengrvis.mobile.session.token", "old-lan-token");
    unclearedInsecureStorage.failures.removeItem = new Error("AsyncStorage raw secret cleanup failure");
    await assert.rejects(
      () => unclearedInsecureAuth.loadSession(),
      assertSessionRecoveryError,
      "stored insecure LAN sessions must fail closed into App recovery if local cleanup fails",
    );
    assert.equal(unclearedInsecureStorage.asyncMap.has("lengrvis.mobile.session"), true);
    assert.equal(unclearedInsecureStorage.secureMap.has("lengrvis.mobile.session.token"), false);

    const expiredStorage = makeStorage();
    const expiredAuth = loadAuth(client, expiredStorage);
    expiredStorage.asyncMap.set(
      "lengrvis.mobile.session",
      JSON.stringify({
        baseUrl: httpsSession.baseUrl,
        baseUrlSecurity: httpsSession.baseUrlSecurity,
        deviceId: httpsSession.deviceId,
        expiresAt: new Date(Date.now() - 1000).toISOString(),
      }),
    );
    expiredStorage.secureMap.set("lengrvis.mobile.session.token", "expired-token");
    assert.equal(await expiredAuth.loadSession(), null);
    assert.equal(expiredStorage.asyncMap.has("lengrvis.mobile.session"), false);
    assert.equal(expiredStorage.secureMap.has("lengrvis.mobile.session.token"), false);

    const orphanStorage = makeStorage();
    const orphanAuth = loadAuth(client, orphanStorage);
    orphanStorage.secureMap.set("lengrvis.mobile.session.token", "orphan-token");
    assert.equal(await orphanAuth.loadSession(), null);
    assert.equal(orphanStorage.secureMap.has("lengrvis.mobile.session.token"), false);

    const unclearedOrphanStorage = makeStorage();
    const unclearedOrphanAuth = loadAuth(client, unclearedOrphanStorage);
    unclearedOrphanStorage.secureMap.set("lengrvis.mobile.session.token", "orphan-token");
    unclearedOrphanStorage.failures.deleteItemAsync = new Error("SecureStore raw token delete failure");
    await assert.rejects(
      () => unclearedOrphanAuth.loadSession(),
      assertSessionRecoveryError,
      "orphan mobile tokens must fail closed into App recovery if secure storage cleanup fails",
    );
    assert.equal(unclearedOrphanStorage.secureMap.has("lengrvis.mobile.session.token"), true);

    expectedPairToken = "query-stripped-token";
    const queryStrippedPaired = await client.pairWithBackend(
      `${server.origin}/copied/path?token=secret-token#pair`,
      "abcd1234ef567890",
      "Phone",
      undefined,
      "claim-secret-for-mobile-smoke-123456",
    );
    assert.equal(server.requests.length, 1, "query-bearing pasted addresses must still call only the pair-confirm endpoint");
    assert.equal(queryStrippedPaired.baseUrl, server.origin);
    assert.equal(queryStrippedPaired.token, "query-stripped-token");
    assert.doesNotMatch(queryStrippedPaired.baseUrl, /secret-token|[?&]token=/);

    expectedPairToken = "paired-token";
    const paired = await client.pairWithBackend(
      `${server.origin}/`,
      "abcd1234ef567890",
      "Phone",
      undefined,
      "claim-secret-for-mobile-smoke-123456",
    );
    assert.equal(server.requests.length, 2, "pairing must reach the local HTTP smoke service");
    assert.equal(paired.baseUrl, server.origin);
    assert.equal(paired.token, expectedPairToken);
    assert.equal(paired.deviceId, "device-1");
    assert.equal(paired.deviceTrust.attestation_verified, false);
    assert.equal(paired.deviceTrust.attestation_status, "not_verified");
    assert.equal(paired.deviceTrust.trust_basis, "pairing_code_tls");
    assert.equal(paired.baseUrlSecurity.kind, "loopbackHttp");
    assert.equal(paired.server.port, Number(new URL(server.origin).port));
    assert.equal(paired.security.transport.httpScheme, "http");
    assert.equal(paired.security.transport.webSocketScheme, "ws");
    assert.equal(paired.security.tls.trustStatus, "not_enabled");

    const approvalInfo = client.approvalWebSocketConnectionInfo(paired);
    assert.equal(approvalInfo.url, `${server.origin.replace("http:", "ws:")}/ws/mobile/approvals`);
    assertWebSocketTokenTransport(approvalInfo, expectedPairToken, { pathname: "/ws/mobile/approvals", label: "approval WebSocket" });
    assertAcceptedWebSocket(await connectWebSocket(approvalInfo.url, approvalInfo.protocols), approvalInfo.protocols[0]);

    const screenInfo = client.remoteScreenWebSocketConnectionInfo(paired);
    assert.equal(screenInfo.url, `${server.origin.replace("http:", "ws:")}/ws/remote/screen`);
    assertWebSocketTokenTransport(screenInfo, expectedPairToken, { pathname: "/ws/remote/screen", label: "remote screen WebSocket" });
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
        baseUrl: httpsSession.baseUrl,
        baseUrlSecurity: httpsSession.baseUrlSecurity,
        deviceId: httpsSession.deviceId,
        token: "legacy-token",
      }),
    );
    const migrated = await migratedAuth.loadSession();
    assert.equal(migrated.token, "legacy-token");
    assert.equal(migrated.baseUrl, "https://example.test:8443");
    assert.equal(migratedStorage.secureMap.get("lengrvis.mobile.session.token"), "legacy-token");
    assert.doesNotMatch(migratedStorage.asyncMap.get("lengrvis.mobile.session"), /legacy-token/);

    expectedPairToken = "stored-token";
    const storedSession = await client.pairWithBackend(
      `${server.origin}/`,
      "abcd1234ef567890",
      "Phone",
      undefined,
      "claim-secret-for-mobile-smoke-123456",
    );
    await migratedAuth.saveSession(storedSession);
    const storedMetadata = JSON.parse(migratedStorage.asyncMap.get("lengrvis.mobile.session"));
    assert.equal(storedMetadata.baseUrl, server.origin);
    assert.equal(storedMetadata.deviceTrust.attestation_verified, false);
    assert.equal(storedMetadata.deviceTrust.trust_basis, "pairing_code_tls");
    assert.equal(migratedStorage.secureMap.get("lengrvis.mobile.session.token"), "stored-token");
    assert.doesNotMatch(migratedStorage.asyncMap.get("lengrvis.mobile.session"), /stored-token/);

    const beforeExpiredPairRequests = server.requests.length;
    expectedPairToken = "expired-pair-token";
    pairResponseOverrides = { expires_in: 0 };
    await assert.rejects(
      () => client.pairWithBackend(
        `${server.origin}/`,
        "abcd1234ef567890",
        "Phone",
        undefined,
        "claim-secret-for-mobile-smoke-123456",
      ),
      (error) => error.name === "AuthExpiredError",
    );
    assert.equal(server.requests.length, beforeExpiredPairRequests + 1);

    expectedPairToken = "invalid-pair-token";
    pairResponseOverrides = { token: "bad token" };
    await assert.rejects(
      () => client.pairWithBackend(
        `${server.origin}/`,
        "abcd1234ef567890",
        "Phone",
        undefined,
        "claim-secret-for-mobile-smoke-123456",
      ),
      (error) => error.name === "BackendHttpError" && error.code === "invalid_pairing_response",
    );
    assert.equal(server.requests.length, beforeExpiredPairRequests + 2);
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
