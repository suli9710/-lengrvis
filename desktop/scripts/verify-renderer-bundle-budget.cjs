const fs = require("node:fs");
const path = require("node:path");

const rendererDir = path.join(__dirname, "..", "dist", "renderer");
const assetsDir = path.join(rendererDir, "assets");

// Async collection states and local-library correctness fixes add a small,
// measured baseline cost. Character stills remain external assets via
// `?no-inline`, so these limits cover application code rather than image data.
const budgets = {
  entryJs: readBudget("LENGRVIS_RENDERER_ENTRY_JS_BUDGET_KB", 235),
  chunkJs: readBudget("LENGRVIS_RENDERER_CHUNK_JS_BUDGET_KB", 160),
  totalJs: readBudget("LENGRVIS_RENDERER_TOTAL_JS_BUDGET_KB", 750),
  totalCss: readBudget("LENGRVIS_RENDERER_TOTAL_CSS_BUDGET_KB", 195),
};

function fail(message) {
  console.error(`Renderer bundle budget failed: ${message}`);
  process.exitCode = 1;
}

function readBudget(name, fallback) {
  const rawValue = process.env[name];
  if (rawValue === undefined || rawValue.trim() === "") return fallback;
  const value = Number(rawValue);
  if (!Number.isFinite(value) || value <= 0) {
    fail(`${name} must be a positive finite number, got ${JSON.stringify(rawValue)}`);
    return null;
  }
  return value;
}

function sizeKb(filePath) {
  return fs.statSync(filePath).size / 1024;
}

if (Object.values(budgets).some((value) => value === null)) {
  return;
}

if (!fs.existsSync(assetsDir)) {
  fail(`missing build output at ${assetsDir}`);
  return;
}

const assets = fs.readdirSync(assetsDir);
const jsFiles = assets.filter((name) => name.endsWith(".js"));
const cssFiles = assets.filter((name) => name.endsWith(".css"));
const entryFiles = jsFiles.filter((name) => /^index-[^.]+\.js$/.test(name));

if (entryFiles.length !== 1) {
  fail(`expected one renderer entry chunk, found ${entryFiles.length}`);
}

const jsSizes = jsFiles.map((name) => ({
  name,
  kb: sizeKb(path.join(assetsDir, name)),
}));
const totalJs = jsSizes.reduce((sum, item) => sum + item.kb, 0);
const totalCss = cssFiles.reduce(
  (sum, name) => sum + sizeKb(path.join(assetsDir, name)),
  0,
);
const largestChunk = jsSizes
  .filter((item) => !entryFiles.includes(item.name))
  .sort((left, right) => right.kb - left.kb)[0];
const entrySize = entryFiles.length === 1
  ? sizeKb(path.join(assetsDir, entryFiles[0]))
  : 0;

if (entrySize > budgets.entryJs) {
  fail(`entry JS is ${entrySize.toFixed(1)} KB (budget ${budgets.entryJs} KB)`);
}
if (largestChunk && largestChunk.kb > budgets.chunkJs) {
  fail(
    `${largestChunk.name} is ${largestChunk.kb.toFixed(1)} KB ` +
      `(chunk budget ${budgets.chunkJs} KB)`,
  );
}
if (totalJs > budgets.totalJs) {
  fail(`total JS is ${totalJs.toFixed(1)} KB (budget ${budgets.totalJs} KB)`);
}
if (totalCss > budgets.totalCss) {
  fail(`total CSS is ${totalCss.toFixed(1)} KB (budget ${budgets.totalCss} KB)`);
}

if (!process.exitCode) {
  console.log(
    `Renderer bundle budget passed: entry ${entrySize.toFixed(1)} KB, ` +
      `largest chunk ${largestChunk?.kb.toFixed(1) ?? "0.0"} KB, ` +
      `total JS ${totalJs.toFixed(1)} KB, CSS ${totalCss.toFixed(1)} KB.`,
  );
}
