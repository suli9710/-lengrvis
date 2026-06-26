const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const mobileRoot = path.resolve(__dirname, "..");
const configs = [
  "android/app/src/main/res/xml/network_security_config.xml",
  "android/app/src/debug/res/xml/network_security_config.xml",
  "android/app/src/debugOptimized/res/xml/network_security_config.xml",
];

for (const relativePath of configs) {
  const source = fs.readFileSync(path.join(mobileRoot, relativePath), "utf8");
  assert.match(source, /<certificates\s+src="system"\s*\/>/, `${relativePath} must retain system trust anchors`);
  assert.doesNotMatch(source, /<certificates\s+src="user"\s*\/>/, `${relativePath} must reject user-installed CAs`);
}

const application = fs.readFileSync(
  path.join(mobileRoot, "android/app/src/main/java/com/lengrvis/approval/MainApplication.kt"),
  "utf8",
);
assert.match(application, /LengrvisLanTrust\.install\(this\)/, "React Native networking must install the pinned OkHttp factory");

const trust = fs.readFileSync(
  path.join(mobileRoot, "android/app/src/main/java/com/lengrvis/approval/LengrvisLanTrust.kt"),
  "utf8",
);
for (const fragment of [
  "OkHttpClientProvider.setOkHttpClientFactory",
  ".sslSocketFactory",
  "AndroidCAStore",
  'alias.startsWith("system:")',
  "hasAnyFingerprint",
  "hostHasFingerprint",
]) {
  assert.ok(trust.includes(fragment), `LAN TLS trust implementation must include ${fragment}`);
}

console.log("[pass] Android LAN TLS source contract");
