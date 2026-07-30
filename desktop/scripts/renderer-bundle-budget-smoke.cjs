const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const verifier = path.join(__dirname, "verify-renderer-bundle-budget.cjs");
const budgetNames = [
  "LENGRVIS_RENDERER_ENTRY_JS_BUDGET_KB",
  "LENGRVIS_RENDERER_CHUNK_JS_BUDGET_KB",
  "LENGRVIS_RENDERER_TOTAL_JS_BUDGET_KB",
  "LENGRVIS_RENDERER_TOTAL_CSS_BUDGET_KB",
];

for (const [name, invalidValue] of [
  [budgetNames[0], "invalid"],
  [budgetNames[1], "0"],
  [budgetNames[2], "-1"],
  [budgetNames[3], "Infinity"],
]) {
  const env = { ...process.env };
  for (const budgetName of budgetNames) delete env[budgetName];
  env[name] = invalidValue;
  const result = spawnSync(process.execPath, [verifier], {
    cwd: path.join(__dirname, ".."),
    encoding: "utf8",
    env,
  });
  assert.notEqual(result.status, 0, `${name}=${invalidValue} must fail closed`);
  assert.match(
    `${result.stdout}\n${result.stderr}`,
    new RegExp(`${name} must be a positive finite number`),
  );
}

console.log("renderer bundle budget configuration smoke passed");
