const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { spawnSync } = require("node:child_process");
const { verifyRendererBundle, budgetDefaults } = require("./verify-renderer-bundle-budget.cjs");
const verifier = path.join(__dirname, "verify-renderer-bundle-budget.cjs");
const budgetNames = Object.values(budgetDefaults).map(([name]) => name);

for (const name of budgetNames) {
  for (const invalidValue of ["invalid", "0", "-1", "Infinity", "NaN"]) {
    const env = { ...process.env };
    for (const budgetName of budgetNames) delete env[budgetName];
    env[name] = invalidValue;
    const result = spawnSync(process.execPath, [verifier], { encoding: "utf8", env });
    assert.notEqual(result.status, 0, `${name}=${invalidValue} must fail closed`);
    assert.match(`${result.stdout}\n${result.stderr}`, new RegExp(`${name} must be a positive finite number`));
  }
}

const root = fs.mkdtempSync(path.join(os.tmpdir(), "lengrvis-renderer-budget-"));
const assets = path.join(root, "assets");
function fixture(sizes = {}, moduleIds = ["node_modules/react-dom/cjs/react-dom-client.production.js", "node_modules/react/index.js", "node_modules/scheduler/index.js"]) {
  fs.rmSync(assets, { recursive: true, force: true });
  fs.mkdirSync(assets);
  const files = { "index-entry.js": 235 * 1024, "react-vendor.js": 210 * 1024,
    "ordinary-a.js": 160 * 1024, "ordinary-b.js": 160 * 1024,
    "ordinary-c.js": 115 * 1024, "styles.css": 195 * 1024, ...sizes };
  for (const [name, bytes] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(assets, name)), { recursive: true });
    fs.writeFileSync(path.join(assets, name), Buffer.alloc(bytes));
  }
  const metadata = { schemaVersion: 1, chunks: { "assets/react-vendor.js": moduleIds },
    hashes: { "assets/react-vendor.js": createHash("sha256").update(fs.readFileSync(path.join(assets, "react-vendor.js"))).digest("hex") } };
  fs.writeFileSync(path.join(root, "renderer-budget-modules.json"), JSON.stringify(metadata));
}
function fails(pattern) {
  const result = verifyRendererBundle(root, {});
  assert(result.errors.some((error) => pattern.test(error)), `${pattern}: ${JSON.stringify(result)}`);
}
try {
  fixture();
  assert.deepEqual(verifyRendererBundle(root, {}).errors, [], "all exact byte boundaries must pass");
  for (const [name, size, pattern] of [
    ["index-entry.js", 235 * 1024 + 1, /entry JS/],
    ["react-vendor.js", 210 * 1024 + 1, /React JS/],
    ["ordinary-a.js", 160 * 1024 + 1, /ordinary chunk JS/],
    ["ordinary-c.js", 115 * 1024 + 1, /total JS/],
    ["styles.css", 195 * 1024 + 1, /total CSS/],
  ]) { fixture({ [name]: size }); fails(pattern); }
  fixture({ "nested/extra.js": 1 }); fails(/total JS/);
  fixture({ "nested/extra.js": 160 * 1024 + 1 }); fails(/ordinary chunk JS/);
  fixture({}, ["src/features/react-disguised.tsx"]); fails(/module ownership/);
  fixture({}, ["node_modules/react/../../src/disguised.js"]); fails(/module ownership/);
  fixture({}, []); fails(/module ownership/);
  fixture({}, ["\0node_modules/react/index.js"]); fails(/module ownership/);
  fixture({ "react-disguised.js": 170 * 1024 }); fails(/expected one dedicated React chunk/); fails(/module ownership/);
  fixture(); fs.unlinkSync(path.join(root, "renderer-budget-modules.json")); fails(/missing or invalid/);
  fixture(); fs.writeFileSync(path.join(root, "renderer-budget-modules.json"), "{"); fails(/missing or invalid/);
  fixture(); fs.writeFileSync(path.join(assets, "react-vendor.js"), Buffer.alloc(210 * 1024, 1)); fails(/stale content hash/);
  fixture();
  const manifestPath = path.join(root, "renderer-budget-modules.json");
  const metadata = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  metadata.schemaVersion = 2;
  fs.writeFileSync(manifestPath, JSON.stringify(metadata)); fails(/module ownership/);
  fixture({ "index-second.js": 1 }); fails(/expected one renderer entry chunk/);
  fixture(); fs.unlinkSync(path.join(assets, "react-vendor.js")); fails(/expected one dedicated React chunk/);
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
console.log("renderer byte budget smoke passed: React ownership/hash, exact boundaries, ordinary chunks, totals and invalid configuration (not runtime performance)");
