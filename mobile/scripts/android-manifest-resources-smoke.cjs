const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const mobileRoot = path.resolve(__dirname, "..");
const manifestPath = path.join(mobileRoot, "android/app/src/main/AndroidManifest.xml");
const manifest = fs.readFileSync(manifestPath, "utf8");
const xmlReferences = [...manifest.matchAll(/@xml\/([A-Za-z0-9_]+)/g)].map((match) => match[1]);

assert.ok(xmlReferences.length > 0, "AndroidManifest.xml should reference XML resources explicitly");
for (const name of xmlReferences) {
  const resourcePath = path.join(mobileRoot, "android/app/src/main/res/xml", `${name}.xml`);
  assert.ok(fs.existsSync(resourcePath), `AndroidManifest.xml references missing @xml/${name}`);
}

for (const relativePath of [
  "android/app/src/debug/AndroidManifest.xml",
  "android/app/src/debugOptimized/AndroidManifest.xml",
]) {
  const source = fs.readFileSync(path.join(mobileRoot, relativePath), "utf8");
  assert.doesNotMatch(source, /SYSTEM_ALERT_WINDOW/, `${relativePath} must not reintroduce overlay permission`);
  assert.doesNotMatch(
    source,
    /usesCleartextTraffic\s*=\s*"true"/,
    `${relativePath} must not permit app-wide cleartext traffic`,
  );
  assert.doesNotMatch(
    source,
    /tools:replace\s*=\s*"android:usesCleartextTraffic"/,
    `${relativePath} must not override the main cleartext policy`,
  );
}

for (const relativePath of [
  "android/app/src/debug/res/xml/network_security_config.xml",
  "android/app/src/debugOptimized/res/xml/network_security_config.xml",
]) {
  const source = fs.readFileSync(path.join(mobileRoot, relativePath), "utf8");
  assert.match(
    source,
    /<base-config\s+cleartextTrafficPermitted="false">/,
    `${relativePath} must fail closed for default cleartext`,
  );
  assert.match(
    source,
    /<domain\s+includeSubdomains="false">10\.0\.2\.2<\/domain>/,
    `${relativePath} may allow emulator cleartext only through network_security_config`,
  );
}

console.log("[pass] Android manifest XML resources exist");
