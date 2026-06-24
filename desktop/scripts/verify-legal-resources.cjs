const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const desktopRoot = path.join(__dirname, "..");
const repoRoot = path.join(desktopRoot, "..");
const requiredFiles = [
  "LICENSE",
  "NOTICE",
  path.join("docs", "legal", "eula.md"),
  path.join("docs", "legal", "privacy-policy.md"),
];

for (const relativePath of requiredFiles) {
  const absolutePath = path.join(repoRoot, relativePath);
  assert.ok(fs.existsSync(absolutePath), `missing legal resource: ${relativePath}`);
  assert.ok(fs.statSync(absolutePath).size > 0, `empty legal resource: ${relativePath}`);
}

const builderConfig = fs.readFileSync(
  path.join(desktopRoot, "electron-builder.yml"),
  "utf8",
);

for (const platform of ["win:", "mac:", "linux:"]) {
  assert.ok(builderConfig.includes(platform), `missing ${platform} package config`);
}
for (const resource of [
  "from: ../docs/legal",
  "from: ../LICENSE",
  "from: ../NOTICE",
]) {
  const occurrences = builderConfig.split(resource).length - 1;
  assert.equal(occurrences, 3, `${resource} must be packaged on all platforms`);
}

const privacyPolicy = fs.readFileSync(
  path.join(repoRoot, "docs", "legal", "privacy-policy.md"),
  "utf8",
);
assert.match(privacyPolicy, /\*\*版本\*\*：v1\.1/);
assert.match(privacyPolicy, /发布候选草案/);

console.log("Legal resources are present and configured for all desktop packages.");
