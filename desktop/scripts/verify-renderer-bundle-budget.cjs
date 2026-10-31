const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

// React 19 rebaseline approved 2026-10-31: measured React ~205.7 KiB,
// total JS ~869.8 KiB after safe optimization. 210/880 KiB leave ~2%/1%
// headroom; entry, ordinary chunks and CSS retain their existing limits.
// These are uncompressed byte gates, NOT startup-time or memory acceptance.
// Windows startup/interaction/memory measurements remain a separate validation requirement.
const budgetDefaults = {
  entryJs: ["LENGRVIS_RENDERER_ENTRY_JS_BUDGET_KB", 235],
  chunkJs: ["LENGRVIS_RENDERER_CHUNK_JS_BUDGET_KB", 160],
  reactJs: ["LENGRVIS_RENDERER_REACT_JS_BUDGET_KB", 210],
  totalJs: ["LENGRVIS_RENDERER_TOTAL_JS_BUDGET_KB", 880],
  totalCss: ["LENGRVIS_RENDERER_TOTAL_CSS_BUDGET_KB", 195],
};

function verifyRendererBundle(rendererDir, env = process.env) {
  const errors = [];
  const budgets = {};
  for (const [key, [name, fallback]] of Object.entries(budgetDefaults)) {
    const raw = env[name];
    const value = raw === undefined || raw.trim() === "" ? fallback : Number(raw);
    if (!Number.isFinite(value) || value <= 0) {
      errors.push(`${name} must be a positive finite number, got ${JSON.stringify(raw)}`);
    }
    budgets[key] = value;
  }
  if (errors.length) return { errors };
  const assetsDir = path.join(rendererDir, "assets");
  if (!fs.existsSync(assetsDir)) return { errors: [`missing build output at ${assetsDir}`] };
  function filesIn(dir, prefix = "") {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const name = prefix + entry.name;
      return entry.isDirectory() ? filesIn(path.join(dir, entry.name), `${name}/`) : [name];
    });
  }
  const files = filesIn(assetsDir);
  const size = (name) => fs.statSync(path.join(assetsDir, name)).size / 1024;
  const js = files.filter((name) => name.endsWith(".js")).map((name) => ({ name, kb: size(name) }));
  const entries = js.filter(({ name }) => /^index-[^.]+\.js$/.test(name));
  const react = js.filter(({ name }) => /^react-[A-Za-z0-9_-]+\.js$/.test(name));
  if (entries.length !== 1) errors.push(`expected one renderer entry chunk, found ${entries.length}`);
  if (react.length !== 1) errors.push(`expected one dedicated React chunk, found ${react.length}`);

  // A filename alone is not evidence: only the build's actual module membership
  // can qualify a chunk for the React allowance. Unknown/missing ownership fails closed.
  let metadata;
  try {
    metadata = JSON.parse(fs.readFileSync(path.join(rendererDir, "renderer-budget-modules.json"), "utf8"));
  } catch {
    errors.push("missing or invalid renderer-budget-modules.json; rebuild the renderer");
  }
  for (const item of react) {
    const modules = metadata?.schemaVersion === 1 && metadata.chunks?.[`assets/${item.name}`];
    const actualHash = createHash("sha256").update(fs.readFileSync(path.join(assetsDir, item.name))).digest("hex");
    if (metadata?.hashes?.[`assets/${item.name}`] !== actualHash) {
      errors.push(`${item.name} cannot use React budget: missing or stale content hash`);
    }
    if (!Array.isArray(modules) || modules.length === 0 || !modules.every((id) =>
      typeof id === "string" && /^node_modules\/(react|react-dom|scheduler)\//.test(id) && !id.split("/").includes("..") && !id.includes("\0") && !id.includes("\\")
    )) {
      errors.push(`${item.name} cannot use React budget: module ownership is missing or includes non-React modules`);
    }
  }
  const ordinary = js.filter((item) => !entries.includes(item) && !react.includes(item));
  const check = (label, value, limit) => {
    if (value > limit) errors.push(`${label} is ${value.toFixed(1)} KiB (budget ${limit} KiB)`);
  };
  for (const item of entries) check("entry JS", item.kb, budgets.entryJs);
  for (const item of react) check(`${item.name} React JS`, item.kb, budgets.reactJs);
  for (const item of ordinary) check(`${item.name} ordinary chunk JS`, item.kb, budgets.chunkJs);
  const totalJs = js.reduce((sum, item) => sum + item.kb, 0);
  const totalCss = files.filter((name) => name.endsWith(".css")).reduce((sum, name) => sum + size(name), 0);
  check("total JS", totalJs, budgets.totalJs);
  check("total CSS", totalCss, budgets.totalCss);
  return { errors, entryJs: entries[0]?.kb ?? 0, reactJs: react[0]?.kb ?? 0,
    largestChunk: Math.max(0, ...ordinary.map((item) => item.kb)), totalJs, totalCss };
}

if (require.main === module) {
  const result = verifyRendererBundle(path.join(__dirname, "..", "dist", "renderer"));
  if (result.errors.length) {
    for (const error of result.errors) console.error(`Renderer bundle budget failed: ${error}`);
    process.exitCode = 1;
  } else {
    console.log(`Renderer bundle budget passed: entry ${result.entryJs.toFixed(1)} KiB, ` +
      `React ${result.reactJs.toFixed(1)} KiB, largest ordinary chunk ${result.largestChunk.toFixed(1)} KiB, ` +
      `total JS ${result.totalJs.toFixed(1)} KiB, CSS ${result.totalCss.toFixed(1)} KiB. ` +
      "Byte budgets only; startup and memory performance are not verified.");
  }
}
module.exports = { verifyRendererBundle, budgetDefaults };
