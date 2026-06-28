const { execFileSync } = require("node:child_process");
const { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");

const root = resolve(__dirname, "..", "..");
const distDir = join(root, "dist");
const releaseDir = join(root, "desktop", "release");
const portableDir = join(distDir, "Lengrvis-win-portable");
const backendExe = join(distDir, "backend.exe");

function readDesktopVersion() {
  const packageJsonPath = join(root, "desktop", "package.json");
  const packageJson = JSON.parse(readFileSync(packageJsonPath, "utf8"));
  const version = packageJson && packageJson.version;
  if (!version || typeof version !== "string" || !String(version).trim()) {
    throw new Error("desktop/package.json has no version field; it is the single source of truth for artifact names.");
  }
  return String(version).trim();
}

const selfExtractingExe = join(distDir, `Lengrvis-${readDesktopVersion()}-x64-self-extracting.exe`);
const portableZip = join(distDir, "Lengrvis-win-portable.zip");
const portableLauncher = join(portableDir, "Lengrvis.exe");
const portableBackendExe = join(portableDir, "resources", "backend", "backend.exe");

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

function verifyPortableZipInnerLauncher(issueList) {
  if (!existsSync(portableZip)) {
    return;
  }
  const extractDir = mkdtempSync(join(tmpdir(), "lengrvis-portable-zip-"));
  try {
    execFileSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        `Expand-Archive -LiteralPath ${powershellString(portableZip)} -DestinationPath ${powershellString(extractDir)} -Force`
      ],
      { encoding: "utf8" }
    );
    const innerLauncher = join(extractDir, "Lengrvis.exe");
    if (!existsSync(innerLauncher)) {
      issueList.push(`Missing portable launcher inside zip: ${portableZip}`);
      return;
    }
    let status = "";
    try {
      status = authenticodeStatus(innerLauncher);
    } catch (error) {
      issueList.push(`${portableZip} (Lengrvis.exe): signature check failed: ${error.message || error}`);
      return;
    }
    if (status !== "Valid") {
      issueList.push(`${portableZip} (Lengrvis.exe): Authenticode status is ${status || "(empty)"}`);
    }
  } finally {
    rmSync(extractDir, { recursive: true, force: true });
  }
}

const releaseExes = collectExeFiles(releaseDir);
const portableExes = collectExeFiles(portableDir);
const files = [
  backendExe,
  selfExtractingExe,
  ...releaseExes,
  ...portableExes
];
const uniqueFiles = [...new Set(files)];
const issues = [];

if (!existsSync(backendExe)) {
  issues.push(`Missing backend executable: ${backendExe}`);
}
if (!existsSync(releaseDir)) {
  issues.push(`Missing Electron release directory: ${releaseDir}`);
}
if (releaseExes.length === 0) {
  issues.push(`No Windows release .exe artifacts found under ${releaseDir}`);
}

if (existsSync(portableDir)) {
  if (!existsSync(portableLauncher)) {
    issues.push(`Missing portable launcher: ${portableLauncher}`);
  }
  if (!existsSync(portableBackendExe)) {
    issues.push(`Missing portable backend executable: ${portableBackendExe}`);
  }
  if (!existsSync(selfExtractingExe)) {
    issues.push(`Missing self-extracting executable: ${selfExtractingExe}`);
  }
  if (portableExes.length === 0) {
    issues.push(`No Windows portable .exe artifacts found under ${portableDir}`);
  }
} else if (portableExes.length > 0) {
  issues.push(`Portable .exe artifacts exist but portable directory is missing: ${portableDir}`);
}

verifyPortableZipInnerLauncher(issues);

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

const summaryParts = ["backend", "electron release"];
if (portableExes.length > 0) {
  summaryParts.push("portable");
}
if (existsSync(selfExtractingExe)) {
  summaryParts.push("self-extracting");
}
console.log(
  `Verified Authenticode signatures for ${uniqueFiles.length} Windows release executable(s) (${summaryParts.join(", ")}).`
);
