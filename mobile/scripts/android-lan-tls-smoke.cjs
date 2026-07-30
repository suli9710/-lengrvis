const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const mobileRoot = path.resolve(__dirname, "..");
const androidRoot = path.join(mobileRoot, "android");

function readMobile(relativePath) {
  return fs.readFileSync(path.join(mobileRoot, relativePath), "utf8");
}

function runGradle(args) {
  const command = process.platform === "win32" ? "cmd.exe" : "./gradlew";
  const commandArgs = process.platform === "win32" ? ["/d", "/s", "/c", "gradlew.bat", ...args] : args;
  const result = childProcess.spawnSync(command, commandArgs, {
    cwd: androidRoot,
    stdio: "inherit",
    shell: false,
  });
  if (result.error) {
    throw result.error;
  }
  process.exit(result.status ?? 1);
}

function assertSourceContract() {
  const configs = [
    "android/app/src/main/res/xml/network_security_config.xml",
    "android/app/src/debug/res/xml/network_security_config.xml",
    "android/app/src/debugOptimized/res/xml/network_security_config.xml",
  ];

  for (const relativePath of configs) {
    const source = readMobile(relativePath);
    assert.match(source, /<certificates\s+src="system"\s*\/>/, `${relativePath} must retain system trust anchors`);
    assert.doesNotMatch(source, /<certificates\s+src="user"\s*\/>/, `${relativePath} must reject user-installed CAs`);
  }

  const application = readMobile("android/app/src/main/java/com/lengrvis/approval/MainApplication.kt");
  assert.match(
    application,
    /LengrvisLanTrust\.install\(this\)/,
    "React Native networking must install the pinned OkHttp factory",
  );

  const trust = readMobile("android/app/src/main/java/com/lengrvis/approval/LengrvisLanTrust.kt");
  for (const fragment of [
    "OkHttpClientProvider.setOkHttpClientFactory",
    ".sslSocketFactory",
    ".addInterceptor",
    ".addNetworkInterceptor",
    "AndroidCAStore",
    'alias.startsWith("system:")',
    "hasAnyFingerprint",
    "hostHasFingerprint",
    "hostHasAnyFingerprintForHost",
    'RECORD_SCHEMA = "tls-pin-record-v1"',
    'STATUS_ACTIVE = "active"',
    'STATUS_NEXT = "next"',
    'STATUS_REVOKED = "revoked"',
    "expiresAtEpochMs > nowEpochMs",
    "originHasFingerprint",
    "certificateAllowedByExactOriginPolicy",
    "isSystemTrusted",
    "verifyPinnedOriginBeforeRequest",
    "stageServerCertificate",
    "activateServerCertificate",
    "revokeServerCertificate",
    "assertServerCertificateTrusted",
    "MAX_RECORDS_PER_ORIGIN",
    "validateRecordSet",
    "usable.size <= 2",
    'CORRUPT_STATE_KEY = "tls_pin_store_corrupt_v1"',
    'GOVERNED_STATE_KEY = "tls_pin_store_governed_v1"',
    "assertRequestTrustStateHealthy",
    "originRecords.none { it.isUsable(now) }",
    "failCorruptStoreLocked",
  ]) {
    assert.ok(trust.includes(fragment), `LAN TLS trust implementation must include ${fragment}`);
  }
  assert.doesNotMatch(
    trust,
    /pins\.put\(host,\s*values\)/,
    "LAN TLS trust must not retain the legacy unbounded host-to-fingerprint array",
  );
  assert.doesNotMatch(
    trust,
    /import java\.net\.URL/,
    "pin persistence and requests must not use a different URL canonicalizer",
  );
  const normalizeOriginStart = trust.indexOf("private fun normalizeHttpsOrigin");
  const normalizeOriginEnd = trust.indexOf("private fun originHost", normalizeOriginStart);
  const normalizeOrigin = trust.slice(normalizeOriginStart, normalizeOriginEnd);
  assert.match(
    normalizeOrigin,
    /value\.toHttpUrlOrNull\(\)/,
    "persisted pin origins must use OkHttp HttpUrl IDN and IPv6 canonicalization",
  );
  assert.match(
    normalizeOrigin,
    /return renderHttpsOrigin\(url\)/,
    "persisted pin origins must use the shared request-origin renderer",
  );
  assert.match(
    trust,
    /canonicalRewriteRequired[\s\S]*source\.optString\("origin"\) != record\.origin[\s\S]*writeRecordsLocked\(context, records\)/,
    "legacy v1 IDN/IPv6 records must be rewritten only after canonical validation",
  );
  assert.match(
    trust,
    /private fun requireStoredHost\(value: String\): String = normalizeHost\(value\)/,
    "legacy host fields must be canonicalized and validated before migration",
  );
  assert.match(
    trust,
    /HttpUrl\.Builder\(\)[\s\S]*\.host\(candidate\)/,
    "host migration must use OkHttp canonicalization without parsing untrusted authority text",
  );
  assert.match(
    normalizeOrigin,
    /url\.encodedPath == "\/"/,
    "pin enrollment must reject URLs that are not bare origins",
  );
  const renderOriginStart = trust.indexOf("private fun renderHttpsOrigin");
  const renderOriginEnd = trust.indexOf("private class LengrvisPinnedTrustManager", renderOriginStart);
  const renderAndRequestOrigin = trust.slice(renderOriginStart, renderOriginEnd);
  assert.match(
    renderAndRequestOrigin,
    /val renderedHost = if \(host\.contains\(':'\)\) "\[\$host\]" else host/,
    "shared origin rendering must bracket canonical IPv6 hosts",
  );
  assert.match(
    renderAndRequestOrigin,
    /val port = if \(url\.port == 443\) "" else ":\$\{url\.port\}"/,
    "shared origin rendering must collapse default HTTPS ports and retain non-default ports",
  );
  assert.match(
    renderAndRequestOrigin,
    /private fun requestOrigin\(url: HttpUrl\): String = renderHttpsOrigin\(url\)/,
    "requests and persisted pins must share the same canonical origin renderer",
  );
  assert.match(
    trust,
    /if \(requireExactOriginPin\) return false/,
    "self-signed TLS fallback must require an active pin for the exact origin",
  );
  assert.ok(
    (trust.match(/requireExactOriginPin = !systemTrusted/g) || []).length >= 3,
    "handshake, pooled-request, and hostname paths must apply the same trust-aware exact-origin policy",
  );
  assert.doesNotMatch(
    trust,
    /catch\s*\([^)]*Exception[^)]*\)\s*\{\s*mutableListOf\(\)\s*\}/s,
    "malformed TLS pin storage must never collapse into an empty trusted state",
  );
  const requestInterceptorStart = trust.indexOf(".addInterceptor");
  const requestInterceptorEnd = trust.indexOf(".addNetworkInterceptor", requestInterceptorStart);
  const requestInterceptor = trust.slice(requestInterceptorStart, requestInterceptorEnd);
  const requestTrustCheck = requestInterceptor.indexOf("assertRequestTrustStateHealthy");
  const guardedRequestProceed = requestInterceptor.indexOf("chain.proceed(request)", requestTrustCheck);
  assert.ok(
    requestTrustCheck >= 0 && guardedRequestProceed > requestTrustCheck,
    "corrupt persisted pin state must be rejected before any HTTPS handshake or pooled request",
  );
  const trustManagerStart = trust.indexOf("override fun checkServerTrusted");
  const trustManagerEnd = trust.indexOf("override fun getAcceptedIssuers", trustManagerStart);
  const trustManager = trust.slice(trustManagerStart, trustManagerEnd);
  assert.ok(
    trustManager.indexOf("assertRequestTrustStateHealthy") < trustManager.indexOf("systemTrustManager.checkServerTrusted"),
    "corrupt persisted pin state must be checked before system-trusted fallback",
  );
  const systemTrustCheck = trustManager.indexOf("systemTrustManager.checkServerTrusted");
  const certificateValidityCheck = trustManager.indexOf("leaf.checkValidity()", systemTrustCheck);
  const exactOriginCheck = trustManager.indexOf("certificateAllowedByExactOriginPolicy", certificateValidityCheck);
  assert.ok(
    systemTrustCheck >= 0 && certificateValidityCheck > systemTrustCheck && exactOriginCheck > certificateValidityCheck,
    "all handshake trust paths must validate lifetime and then apply the exact-origin boundary",
  );
  assert.match(
    trustManager,
    /requireExactOriginPin = !systemTrusted/,
    "system-trusted and self-signed handshake paths must use the same exact-origin policy with pin-required mode",
  );
  assert.doesNotMatch(
    trustManager,
    /systemTrustManager\.checkServerTrusted\(chain, authType\)[\s\S]{0,100}\breturn\b/,
    "system trust success must not return before the exact-origin policy check",
  );

  const networkVerifierStart = trust.indexOf("private fun verifyPinnedOriginBeforeRequest");
  const networkVerifierEnd = trust.indexOf("private fun requestOrigin", networkVerifierStart);
  const networkVerifier = trust.slice(networkVerifierStart, networkVerifierEnd);
  assert.match(
    networkVerifier,
    /firstOrNull\(\)\s*\?: throw SSLPeerUnverifiedException/,
    "a pooled HTTPS connection without a peer certificate must fail closed",
  );
  assert.ok(
    networkVerifier.indexOf("leaf.checkValidity()") < networkVerifier.indexOf("isSystemTrusted(certificateChain)") &&
      networkVerifier.indexOf("isSystemTrusted(certificateChain)") <
        networkVerifier.indexOf("certificateAllowedByExactOriginPolicy"),
    "pooled connections must validate lifetime, classify system trust, and then enforce exact origin",
  );
  assert.match(
    networkVerifier,
    /requireExactOriginPin = !systemTrusted/,
    "pooled self-signed connections must require an exact-origin pin",
  );
  assert.doesNotMatch(
    networkVerifier,
    /firstOrNull\(\)\s*\?:\s*return|if\s*\(trustManager\.isSystemTrusted\([^)]*\)\)\s*(?:return|\{[^}]*\breturn\b)/s,
    "pooled TLS validation must not contain an early-return trust bypass",
  );

  const verifierStart = trust.indexOf("private class LengrvisPinnedHostnameVerifier");
  const verifierEnd = trust.indexOf("private fun sha256", verifierStart);
  assert.notEqual(verifierStart, -1, "LAN TLS trust implementation must include a hostname verifier");
  assert.notEqual(verifierEnd, -1, "LAN TLS trust implementation must keep sha256 outside the hostname verifier");
  const verifier = trust.slice(verifierStart, verifierEnd);
  assert.ok(
    verifier.includes("val expectedOrigin = LengrvisTlsOriginScope.get() ?: return false") &&
      verifier.includes("private val trustManager: LengrvisPinnedTrustManager"),
    "hostname verification must fail closed without the request exact origin",
  );
  assert.match(
    verifier,
    /certificateAllowedByExactOriginPolicy\(\s*context,\s*expectedOrigin,\s*fingerprint,\s*requireExactOriginPin = !systemTrusted/,
    "hostname verifier must enforce the exact origin including port",
  );
  assert.doesNotMatch(
    verifier,
    /hostHas(?:AnyFingerprintForHost|Fingerprint)\(/,
    "hostname verifier must not collapse origin-scoped pins to host-only checks",
  );
  assert.ok(
    verifier.indexOf("leaf.checkValidity()") < verifier.indexOf("isSystemTrusted(certificateChain)") &&
      verifier.indexOf("isSystemTrusted(certificateChain)") < verifier.indexOf("certificateAllowedByExactOriginPolicy"),
    "hostname verification must validate lifetime and classify trust before exact-origin authorization",
  );
  assert.match(
    verifier,
    /requireExactOriginPin = !systemTrusted/,
    "hostname verification must require an exact-origin pin on the self-signed path",
  );

  const instrumentation = readMobile(
    "android/app/src/androidTest/java/com/lengrvis/approval/LengrvisLanTrustInstrumentedTest.kt",
  );
  for (const fragment of [
    "lengrvisBaseUrl",
    "lengrvisFingerprintSha256",
    "assertTlsHandshakeFails",
    "wrongFingerprint(fingerprintSha256)",
    "LengrvisLanTrust.trustServerCertificate(context, baseUrl, fingerprintSha256)",
    "pinLifecycleSupportsOverlapPromotionExpiryAndTargetedRevocation",
    "okHttpOriginCanonicalizerUnifiesIdnIpv6AndPortForms",
    "pinnedIdnOriginRejectsReplacementSystemCertificate",
    "legacyV1UnicodePinMigratesToCanonicalOriginWithoutTrustWidening",
    "legacyV1UnicodePinWithMismatchedHostFailsClosed",
    "systemTrustedCertificatePinnedOnAnotherOriginCannotDowngradeExactOrigin",
    "xn--bcher-kva.example",
    "2001:db8::1",
    "exactOriginPolicySeparatesSystemAndPinnedCertificatesAcrossPorts",
    "expiredPinFailsClosedWithoutAutomaticRenewal",
    "malformedMultiPinStoreBlocksRequestsUntilExplicitRepair",
    "legacyPinStoreBlocksRequestsUntilExplicitRepair",
    'getString("tls_pin_store_corrupt_v1", null)',
    "LengrvisLanTrust.revokeServerCertificate",
    "LengrvisLanTrust.activateServerCertificate",
    "OkHttpClientProvider.createClient(context)",
    "/api/health",
    "/api/pair/confirm",
    "/ws/mobile/approvals",
    'connected.contains("\\"type\\":\\"connected\\"")',
  ]) {
    assert.ok(instrumentation.includes(fragment), `connected LAN TLS instrumentation must include ${fragment}`);
  }

  const packageJson = JSON.parse(readMobile("package.json"));
  assert.equal(
    packageJson.scripts["gate:android-instrumentation-compile"],
    "node scripts/android-lan-tls-smoke.cjs --compile-instrumentation",
    "mobile/package.json must expose a PR-safe androidTest compilation gate",
  );
  assert.equal(
    packageJson.scripts["gate:android-connected-lan-tls"],
    "node scripts/android-lan-tls-smoke.cjs --connected",
    "mobile/package.json must expose the release-only connected LAN TLS instrumentation gate",
  );

  console.log("[pass] Android LAN TLS source and connected-instrumentation contract");
}

function runInstrumentationCompileGate() {
  runGradle([":app:assembleDebug", ":app:assembleDebugAndroidTest", "--no-daemon", "--stacktrace"]);
}

function runConnectedGate() {
  const baseUrl = (process.env.LENGRVIS_ANDROID_LAN_TLS_BASE_URL || "").trim();
  const fingerprint = (process.env.LENGRVIS_ANDROID_LAN_TLS_FINGERPRINT_SHA256 || "").trim();
  const pairCode = (process.env.LENGRVIS_ANDROID_LAN_TLS_PAIR_CODE || "").trim();
  const pairClaimSecret = (process.env.LENGRVIS_ANDROID_LAN_TLS_PAIR_CLAIM_SECRET || "").trim();
  const normalizedFingerprint = fingerprint.replaceAll(":", "");

  if (!baseUrl || !fingerprint) {
    throw new Error(
      [
        "Connected LAN TLS instrumentation requires an Android device/emulator plus:",
        "  LENGRVIS_ANDROID_LAN_TLS_BASE_URL=https://...",
        "  LENGRVIS_ANDROID_LAN_TLS_FINGERPRINT_SHA256=<64 hex chars, colons optional>",
        "  LENGRVIS_ANDROID_LAN_TLS_PAIR_CODE=<optional pre-created pairing code>",
        "  LENGRVIS_ANDROID_LAN_TLS_PAIR_CLAIM_SECRET=<required with a pre-created pairing code>",
        "This release/evidence gate is intentionally not run by PR CI.",
      ].join("\n"),
    );
  }
  assert.match(baseUrl, /^https:\/\//, "Connected LAN TLS instrumentation must target an HTTPS backend URL");
  assert.match(
    normalizedFingerprint,
    /^[A-Fa-f0-9]{64}$/,
    "LENGRVIS_ANDROID_LAN_TLS_FINGERPRINT_SHA256 must be a SHA-256 certificate fingerprint",
  );
  assert.equal(
    Boolean(pairCode),
    Boolean(pairClaimSecret),
    "LENGRVIS_ANDROID_LAN_TLS_PAIR_CODE and LENGRVIS_ANDROID_LAN_TLS_PAIR_CLAIM_SECRET must be provided together",
  );

  const gradleArgs = [
    ":app:connectedDebugAndroidTest",
    "-Pandroid.testInstrumentationRunnerArguments.class=com.lengrvis.approval.LengrvisLanTrustInstrumentedTest",
    `-Pandroid.testInstrumentationRunnerArguments.lengrvisBaseUrl=${baseUrl}`,
    `-Pandroid.testInstrumentationRunnerArguments.lengrvisFingerprintSha256=${fingerprint}`,
    "--no-daemon",
    "--stacktrace",
  ];
  if (pairCode) {
    gradleArgs.splice(4, 0, `-Pandroid.testInstrumentationRunnerArguments.lengrvisPairCode=${pairCode}`);
    gradleArgs.splice(
      5,
      0,
      `-Pandroid.testInstrumentationRunnerArguments.lengrvisPairClaimSecret=${pairClaimSecret}`,
    );
  }
  runGradle(gradleArgs);
}

if (process.argv.includes("--compile-instrumentation")) {
  runInstrumentationCompileGate();
} else if (process.argv.includes("--connected")) {
  runConnectedGate();
} else {
  assertSourceContract();
}
