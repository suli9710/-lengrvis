const fs = require("node:fs");
const path = require("node:path");

const args = new Set(process.argv.slice(2));
const desktopRoot = path.resolve(__dirname, "..");
const distRoot = path.join(desktopRoot, "dist");

function walk(root) {
  if (!fs.existsSync(root)) {
    throw new Error(`Desktop dist is missing. Run npm --prefix desktop run build first: ${root}`);
  }
  const entries = [];
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
      } else if (entry.isFile()) {
        entries.push(fullPath);
      }
    }
  }
  return entries;
}

function relative(filePath) {
  return path.relative(desktopRoot, filePath).replace(/\\/g, "/");
}

function scanDist(root) {
  const findings = [];
  for (const filePath of walk(root)) {
    const ext = path.extname(filePath).toLowerCase();
    if (ext === ".map") {
      findings.push(`${relative(filePath)} is a source map file`);
      continue;
    }
    if (ext === ".js" || ext === ".css") {
      const text = fs.readFileSync(filePath, "utf8");
      if (text.includes("sourceMappingURL=")) {
        findings.push(`${relative(filePath)} contains sourceMappingURL`);
      }
    }
  }
  return findings;
}

if (!args.has("--desktop-dist")) {
  throw new Error("Usage: node scripts/source-map-policy-smoke.cjs --desktop-dist");
}

const findings = scanDist(distRoot);
if (findings.length > 0) {
  console.error("Release source map policy failed:");
  for (const finding of findings.slice(0, 20)) {
    console.error(` - ${finding}`);
  }
  if (findings.length > 20) {
    console.error(` - ... ${findings.length - 20} more`);
  }
  process.exit(1);
}

console.log("Source map policy smoke passed: desktop dist has no .map files or sourceMappingURL references.");
