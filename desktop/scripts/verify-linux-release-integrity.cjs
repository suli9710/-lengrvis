const { createHash } = require("node:crypto");
const { existsSync, readFileSync, readdirSync, writeFileSync } = require("node:fs");
const { join, relative, resolve, sep } = require("node:path");

const args = new Set(process.argv.slice(2));
const shouldWrite = args.has("--write");
const root = resolve(__dirname, "..", "..");
const releaseDir = join(root, "desktop", "release");
const manifestPath = join(releaseDir, "lengrvis-linux-checksums.sha256");
const requiredFiles = [join(root, "dist", "backend"), join(root, "dist", "backend-capabilities.json")];
const issues = [];

function collect(dir, predicate) {
  if (!existsSync(dir)) return [];
  const entries = readdirSync(dir, { withFileTypes: true });
  const matches = [];
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      matches.push(...collect(path, predicate));
    } else if (entry.isFile() && predicate(path, entry.name)) {
      matches.push(path);
    }
  }
  return matches;
}

function rel(path) {
  return relative(root, path).split(sep).join("/");
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

const releaseFiles = collect(
  releaseDir,
  (_path, name) => name.endsWith(".AppImage") || name.endsWith(".AppImage.blockmap") || name === "latest-linux.yml"
);

if (!existsSync(releaseDir)) {
  issues.push(`Missing Electron release directory: ${releaseDir}`);
}
if (releaseFiles.filter((file) => file.endsWith(".AppImage")).length < 1) {
  issues.push(`No Linux AppImage artifacts found under ${releaseDir}`);
}

const files = [...requiredFiles, ...releaseFiles].filter((file, index, all) => all.indexOf(file) === index);
for (const file of files) {
  if (!existsSync(file)) {
    issues.push(`Missing required integrity file: ${file}`);
  }
}

if (issues.length === 0 && shouldWrite) {
  const lines = files
    .map((file) => `${sha256(file)}  ${rel(file)}`)
    .sort((a, b) => a.localeCompare(b));
  writeFileSync(manifestPath, `${lines.join("\n")}\n`, "utf8");
}

if (!existsSync(manifestPath)) {
  issues.push(`Missing Linux checksum manifest: ${manifestPath}`);
}

if (issues.length === 0) {
  const expected = new Map();
  for (const rawLine of readFileSync(manifestPath, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const match = /^([a-fA-F0-9]{64})\s+\*?(.+)$/.exec(line);
    if (!match) {
      issues.push(`Malformed checksum line: ${rawLine}`);
      continue;
    }
    expected.set(match[2].trim().replaceAll("\\", "/"), match[1].toLowerCase());
  }

  for (const file of files) {
    const relativePath = rel(file);
    const expectedHash = expected.get(relativePath);
    if (!expectedHash) {
      issues.push(`Checksum manifest is missing ${relativePath}`);
      continue;
    }
    const actualHash = sha256(file);
    if (actualHash !== expectedHash) {
      issues.push(`${relativePath}: expected ${expectedHash}, got ${actualHash}`);
    }
  }
}

if (issues.length > 0) {
  console.error("Linux release integrity verification failed:");
  for (const issue of issues) {
    console.error(` - ${issue}`);
  }
  process.exit(1);
}

console.log(`Verified Linux release checksum manifest: ${rel(manifestPath)} (${files.length} artifact(s)).`);
