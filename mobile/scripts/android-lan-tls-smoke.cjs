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
    "AndroidCAStore",
    'alias.startsWith("system:")',
    "hasAnyFingerprint",
    "hostHasFingerprint",
    "hostHasAnyFingerprintForHost",
  ]) {
    assert.ok(trust.includes(fragment), `LAN TLS trust implementation must include ${fragment}`);
  }

  const verifierStart = trust.indexOf("private class LengrvisPinnedHostnameVerifier");
  const verifierEnd = trust.indexOf("private fun sha256", verifierStart);
  assert.notEqual(verifierStart, -1, "LAN TLS trust implementation must include a hostname verifier");
  assert.notEqual(verifierEnd, -1, "LAN TLS trust implementation must keep sha256 outside the hostname verifier");
  const verifier = trust.slice(verifierStart, verifierEnd);
  assert.ok(
    verifier.includes("hostHasAnyFingerprintForHost(context, hostname)"),
    "pinned hosts must require the presented certificate to match a host pin",
  );
  assert.match(
    verifier,
    /hostHasFingerprint\(context,\s*hostname,\s*fingerprint\)/,
    "hostname verifier must check host-specific pins",
  );
  assert.match(
    verifier,
    /!\s*LengrvisLanTrust\.hasAnyFingerprint\(context,\s*fingerprint\)/,
    "hostname verifier must reject a cert pinned for another host",
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
  const normalizedFingerprint = fingerprint.replaceAll(":", "");

  if (!baseUrl || !fingerprint) {
    throw new Error(
      [
        "Connected LAN TLS instrumentation requires an Android device/emulator plus:",
        "  LENGRVIS_ANDROID_LAN_TLS_BASE_URL=https://...",
        "  LENGRVIS_ANDROID_LAN_TLS_FINGERPRINT_SHA256=<64 hex chars, colons optional>",
        "  LENGRVIS_ANDROID_LAN_TLS_PAIR_CODE=<optional pre-created pairing code>",
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
