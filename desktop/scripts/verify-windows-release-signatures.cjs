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

function requiredEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim() || /^REPLACE_/i.test(value.trim())) {
    throw new Error(`Missing non-placeholder environment variable: ${name}`);
  }
  return value.trim();
}

const expectedPublisherName = requiredEnv("AZURE_TRUSTED_SIGNING_PUBLISHER_NAME");
const expectedCertificateThumbprint = requiredEnv("AZURE_TRUSTED_SIGNING_CERTIFICATE_THUMBPRINT")
  .replace(/\s/g, "")
  .toUpperCase();

function authenticodeDetails(path) {
  const output = execFileSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      [
        `$signature = Get-AuthenticodeSignature -LiteralPath ${powershellString(path)}`,
        "$signer = $signature.SignerCertificate",
        "$timestamp = $signature.TimeStamperCertificate",
        "[ordered]@{",
        "Status = $signature.Status.ToString();",
        "Subject = if ($signer) { $signer.Subject } else { '' };",
        "Thumbprint = if ($signer) { $signer.Thumbprint } else { '' };",
        "Issuer = if ($signer) { $signer.Issuer } else { '' };",
        "NotAfter = if ($signer) { $signer.NotAfter.ToUniversalTime().ToString('o') } else { '' };",
        "TimestampSubject = if ($timestamp) { $timestamp.Subject } else { '' };",
        "TimestampThumbprint = if ($timestamp) { $timestamp.Thumbprint } else { '' }",
        "} | ConvertTo-Json -Compress"
      ].join(" ")
    ],
    { encoding: "utf8" }
  ).trim();
  return JSON.parse(output);
}

function validateAuthenticodeDetails(path, details, issueList) {
  const status = String(details.Status || "");
  const subject = String(details.Subject || "");
  const thumbprint = String(details.Thumbprint || "").replace(/\s/g, "").toUpperCase();
  const timestampSubject = String(details.TimestampSubject || "");
  if (status !== "Valid") {
    issueList.push(`${path}: Authenticode status is ${status || "(empty)"}`);
  }
  if (!subject.includes(expectedPublisherName)) {
    issueList.push(`${path}: signer subject does not include expected publisher ${expectedPublisherName}`);
  }
  if (thumbprint !== expectedCertificateThumbprint) {
    issueList.push(`${path}: signer thumbprint does not match AZURE_TRUSTED_SIGNING_CERTIFICATE_THUMBPRINT`);
  }
  if (!timestampSubject.trim()) {
    issueList.push(`${path}: Authenticode timestamp certificate is missing`);
  }
  return { status, subject, thumbprint, timestampSubject };
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
    let details = {};
    try {
      details = authenticodeDetails(innerLauncher);
    } catch (error) {
      issueList.push(`${portableZip} (Lengrvis.exe): signature check failed: ${error.message || error}`);
      return;
    }
    validateAuthenticodeDetails(`${portableZip} (Lengrvis.exe)`, details, issueList);
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
  let details = {};
  try {
    details = authenticodeDetails(file);
  } catch (error) {
    issues.push(`${file}: signature check failed: ${error.message || error}`);
    continue;
  }
  const summary = validateAuthenticodeDetails(file, details, issues);
  console.log(
    `Checked signature: ${file} subject=${summary.subject || "(empty)"} thumbprint=${summary.thumbprint || "(empty)"}`
  );
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
