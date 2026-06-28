const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const scriptPath = path.join(__dirname, "verify-windows-release-signatures.cjs");
const script = fs.readFileSync(scriptPath, "utf8");
const normalized = script.replace(/\\/g, "/");

assert.ok(normalized.includes("Lengrvis-win-portable"), "must verify portable directory executables");
assert.ok(normalized.includes("x64-self-extracting.exe"), "must verify the self-extracting executable");
assert.ok(normalized.includes("Lengrvis.exe"), "must verify the portable launcher");
assert.ok(
  normalized.includes('"resources", "backend", "backend.exe"') ||
    normalized.includes("resources/backend/backend.exe"),
  "must verify the portable backend executable"
);
assert.ok(normalized.includes("Get-AuthenticodeSignature"), "must use Authenticode verification");
assert.ok(
  normalized.includes('status !== "Valid"'),
  "must fail closed on non-Valid Authenticode status"
);

console.log("windows-release-signatures-smoke: ok");
