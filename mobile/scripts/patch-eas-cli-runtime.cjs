const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

const EXPECTED_EAS_VERSION = "22.2.0";
const ORIGINAL_IMPORT =
  'const ts_deepmerge_1 = tslib_1.__importDefault(require("ts-deepmerge"));';
const PATCHED_IMPORT = 'const ts_deepmerge_1 = require("ts-deepmerge");';
const ORIGINAL_CALL =
  "const mergedConfig = (0, ts_deepmerge_1.default)(baseExpoConfig, expoConfig);";
const PATCHED_CALL =
  "const mergedConfig = (0, ts_deepmerge_1.merge)(baseExpoConfig, expoConfig);";

function occurrenceCount(source, marker) {
  return source.split(marker).length - 1;
}

function fail(message) {
  throw new Error(`eas-cli compatibility patch failed: ${message}`);
}

const mobileRoot = path.resolve(__dirname, "..");
const expectedEasPackagePath = path.join(
  mobileRoot,
  "node_modules",
  "eas-cli",
  "package.json",
);
if (!fs.existsSync(expectedEasPackagePath)) {
  fail(
    "mobile/node_modules/eas-cli is missing; install the locked dev dependencies",
  );
}
const easPackagePath = fs.realpathSync(
  require.resolve("eas-cli/package.json", { paths: [mobileRoot] }),
);
if (easPackagePath !== fs.realpathSync(expectedEasPackagePath)) {
  fail("refusing to patch an eas-cli installation outside mobile/node_modules");
}
const easPackage = JSON.parse(fs.readFileSync(easPackagePath, "utf8"));
if (easPackage.version !== EXPECTED_EAS_VERSION) {
  fail(
    `expected eas-cli ${EXPECTED_EAS_VERSION}, received ${easPackage.version}; review the patch before upgrading`,
  );
}

const requireFromEas = createRequire(easPackagePath);
const deepMerge = requireFromEas("ts-deepmerge");
if (typeof deepMerge.merge !== "function") {
  fail("the installed ts-deepmerge package does not expose the v8 merge function");
}

const targetPath = path.join(
  path.dirname(easPackagePath),
  "build",
  "commandUtils",
  "new",
  "projectFiles.js",
);
let source = fs.readFileSync(targetPath, "utf8");
const alreadyPatched =
  occurrenceCount(source, PATCHED_IMPORT) === 1 &&
  occurrenceCount(source, PATCHED_CALL) === 1 &&
  occurrenceCount(source, ORIGINAL_IMPORT) === 0 &&
  occurrenceCount(source, ORIGINAL_CALL) === 0;

if (process.argv.includes("--check")) {
  if (!alreadyPatched) {
    fail("the installed eas-cli runtime is not patched exactly once");
  }
  console.log("eas-cli-runtime-patch: ok");
  process.exit(0);
}

if (!alreadyPatched) {
  if (
    occurrenceCount(source, ORIGINAL_IMPORT) !== 1 ||
    occurrenceCount(source, ORIGINAL_CALL) !== 1 ||
    occurrenceCount(source, PATCHED_IMPORT) !== 0 ||
    occurrenceCount(source, PATCHED_CALL) !== 0
  ) {
    fail("upstream projectFiles.js no longer matches the reviewed source contract");
  }
  source = source
    .replace(ORIGINAL_IMPORT, PATCHED_IMPORT)
    .replace(ORIGINAL_CALL, PATCHED_CALL);
  fs.writeFileSync(targetPath, source, "utf8");
}

console.log("eas-cli-runtime-patch: applied");
