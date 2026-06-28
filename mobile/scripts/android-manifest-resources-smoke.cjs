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

console.log("[pass] Android manifest XML resources exist");
