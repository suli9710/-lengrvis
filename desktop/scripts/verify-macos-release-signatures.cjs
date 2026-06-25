const { execFileSync } = require("node:child_process");
const { existsSync, readdirSync } = require("node:fs");
const { join, resolve } = require("node:path");

if (process.platform !== "darwin") {
  console.error("macOS release signature verification must run on a macOS runner.");
  process.exit(2);
}

const root = resolve(__dirname, "..", "..");
const releaseDir = join(root, "desktop", "release");
const issues = [];

function collect(dir, predicate) {
  if (!existsSync(dir)) return [];
  const entries = readdirSync(dir, { withFileTypes: true });
  const matches = [];
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (predicate(path, entry)) {
        matches.push(path);
      } else {
        matches.push(...collect(path, predicate));
      }
    } else if (entry.isFile() && predicate(path, entry)) {
      matches.push(path);
    }
  }
  return matches;
}

function run(label, command, args) {
  try {
    execFileSync(command, args, { stdio: "pipe" });
  } catch (error) {
    const output = [
      error.stdout ? String(error.stdout).trim() : "",
      error.stderr ? String(error.stderr).trim() : "",
      error.message || ""
    ]
      .filter(Boolean)
      .join("\n");
    issues.push(`${label} failed: ${output}`);
  }
}

const apps = collect(releaseDir, (path, entry) => entry.isDirectory() && path.endsWith(".app"));
const dmgs = collect(releaseDir, (path, entry) => entry.isFile() && path.endsWith(".dmg"));

if (!existsSync(releaseDir)) {
  issues.push(`Missing Electron release directory: ${releaseDir}`);
}
if (apps.length < 1) {
  issues.push(`No .app bundles found under ${releaseDir}`);
}
if (dmgs.length < 1) {
  issues.push(`No .dmg artifacts found under ${releaseDir}`);
}

for (const app of apps) {
  run(`codesign ${app}`, "codesign", ["--verify", "--deep", "--strict", "--verbose=2", app]);
  run(`spctl ${app}`, "spctl", ["--assess", "--type", "execute", "--verbose=4", app]);
}

for (const dmg of dmgs) {
  run(`stapler ${dmg}`, "xcrun", ["stapler", "validate", dmg]);
  run(`spctl ${dmg}`, "spctl", ["--assess", "--type", "open", "--context", "context:primary-signature", "--verbose=4", dmg]);
}

if (issues.length > 0) {
  console.error("Signed macOS release artifact verification failed:");
  for (const issue of issues) {
    console.error(` - ${issue}`);
  }
  process.exit(1);
}

console.log(`Verified macOS signatures/notarization for ${apps.length} app bundle(s) and ${dmgs.length} dmg artifact(s).`);
