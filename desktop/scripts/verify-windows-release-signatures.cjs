const { execFileSync } = require("node:child_process");
const { existsSync, readdirSync } = require("node:fs");
const { join, resolve } = require("node:path");

const root = resolve(__dirname, "..", "..");
const releaseDir = join(root, "desktop", "release");
const backendExe = join(root, "dist", "backend.exe");

function collectExeFiles(dir) {
  if (!existsSync(dir)) return [];
  const entries = readdirSync(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectExeFiles(path));
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith(".exe")) {
      files.push(path);
    }
  }
  return files;
}

function powershellString(value) {
  return `'${value.replace(/'/g, "''")}'`;
}

function authenticodeStatus(path) {
  return execFileSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      `(Get-AuthenticodeSignature -LiteralPath ${powershellString(path)}).Status.ToString()`
    ],
    { encoding: "utf8" }
  ).trim();
}

const files = [backendExe, ...collectExeFiles(releaseDir)];
const uniqueFiles = [...new Set(files)];
const issues = [];

if (!existsSync(backendExe)) {
  issues.push(`Missing backend executable: ${backendExe}`);
}
if (!existsSync(releaseDir)) {
  issues.push(`Missing Electron release directory: ${releaseDir}`);
}
if (uniqueFiles.length <= 1) {
  issues.push(`No Windows release .exe artifacts found under ${releaseDir}`);
}

for (const file of uniqueFiles) {
  if (!existsSync(file)) continue;
  let status = "";
  try {
    status = authenticodeStatus(file);
  } catch (error) {
    issues.push(`${file}: signature check failed: ${error.message || error}`);
    continue;
  }
  if (status !== "Valid") {
    issues.push(`${file}: Authenticode status is ${status || "(empty)"}`);
  }
}

if (issues.length > 0) {
  console.error("Signed Windows release artifact verification failed:");
  for (const issue of issues) {
    console.error(` - ${issue}`);
  }
  process.exit(1);
}

console.log(`Verified Authenticode signatures for ${uniqueFiles.length} Windows release executable(s).`);
