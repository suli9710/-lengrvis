const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRequire } = require("node:module");
const { execFileSync } = require("node:child_process");

async function main() {
  const mobileRoot = path.resolve(__dirname, "..");
  const patchScript = path.join(__dirname, "patch-eas-cli-runtime.cjs");
  execFileSync(process.execPath, [patchScript, "--check"], { stdio: "inherit" });

  const packageJson = JSON.parse(
    fs.readFileSync(path.join(mobileRoot, "package.json"), "utf8"),
  );
  const expectedEasPackagePath = path.join(
    mobileRoot,
    "node_modules",
    "eas-cli",
    "package.json",
  );
  assert.equal(fs.existsSync(expectedEasPackagePath), true);
  const easPackagePath = fs.realpathSync(
    require.resolve("eas-cli/package.json", { paths: [mobileRoot] }),
  );
  assert.equal(easPackagePath, fs.realpathSync(expectedEasPackagePath));
  const easPackage = JSON.parse(fs.readFileSync(easPackagePath, "utf8"));
  assert.equal(easPackage.version, packageJson.devDependencies["eas-cli"]);

  const easBin = path.join(path.dirname(easPackagePath), "bin", "run");
  const versionOutput = execFileSync(process.execPath, [easBin, "--version"], {
    encoding: "utf8",
  });
  assert.match(versionOutput, /eas-cli\/22\.2\.0/);
  const buildHelp = execFileSync(process.execPath, [easBin, "build", "--help"], {
    encoding: "utf8",
  });
  assert.match(buildHelp, /start a build/);

  const requireFromEas = createRequire(easPackagePath);
  const tslib = requireFromEas("tslib");
  const minimatch = tslib.__importDefault(requireFromEas("minimatch")).default;
  const easMinimatchPackage = requireFromEas("minimatch/package.json");
  assert.equal(easMinimatchPackage.version, "5.1.9");
  assert.equal(typeof minimatch, "function");
  assert.equal(minimatch("com.lengrvis.companion", "com.lengrvis.*"), true);

  const oclifPackagePath = requireFromEas.resolve("@oclif/core/package.json");
  const requireFromOclif = createRequire(oclifPackagePath);
  const oclifMinimatch = requireFromOclif("minimatch");
  const oclifMinimatchPackage = requireFromOclif("minimatch/package.json");
  assert.equal(oclifMinimatchPackage.version, "10.2.6");
  assert.equal(typeof oclifMinimatch.minimatch, "function");
  assert.equal(oclifMinimatch.minimatch("build:android", "build:*"), true);

  const deepMerge = requireFromEas("ts-deepmerge");
  assert.equal(typeof deepMerge.merge, "function");
  assert.deepEqual(
    deepMerge.merge({ nested: { retained: true } }, { nested: { added: true } }),
    { nested: { retained: true, added: true } },
  );

  const projectFilesPath = path.join(
    path.dirname(easPackagePath),
    "build",
    "commandUtils",
    "new",
    "projectFiles.js",
  );
  const projectFiles = require(projectFilesPath);
  const temporaryRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), "lengrvis-eas-compat-"),
  );
  try {
    fs.writeFileSync(
      path.join(temporaryRoot, "app.json"),
      JSON.stringify({ expo: { extra: { retained: true } } }),
      "utf8",
    );
    await projectFiles.generateAppConfigAsync(temporaryRoot, {
      id: "00000000-0000-0000-0000-000000000000",
      name: "Lengrvis",
      ownerAccount: { name: "lengrvis" },
      slug: "companion",
    });
    const generated = JSON.parse(
      fs.readFileSync(path.join(temporaryRoot, "app.json"), "utf8"),
    );
    assert.equal(generated.expo.extra.retained, true);
    assert.equal(
      generated.expo.extra.eas.projectId,
      "00000000-0000-0000-0000-000000000000",
    );
    assert.equal(generated.expo.android.package, "com.lengrvis.companion");
  } finally {
    const resolvedTemporaryRoot = path.resolve(temporaryRoot);
    const resolvedOsTemp = `${path.resolve(os.tmpdir())}${path.sep}`;
    assert.equal(resolvedTemporaryRoot.startsWith(resolvedOsTemp), true);
    fs.rmSync(resolvedTemporaryRoot, { recursive: true, force: true });
  }

  console.log("eas-cli-compat-smoke: ok");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
