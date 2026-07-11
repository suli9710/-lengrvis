const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const hardeningPlugin = require("../plugins/withAndroidRemoteControlHardening");

async function main() {
  assert.equal(
    typeof hardeningPlugin.writeNetworkSecurityConfigs,
    "function",
    "the Android hardening plugin must write each source-set network security config explicitly",
  );

  const platformProjectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "lengrvis-android-network-security-"));
  try {
    await hardeningPlugin.writeNetworkSecurityConfigs(platformProjectRoot);

    const read = (sourceSet) =>
      fs.readFileSync(
        path.join(platformProjectRoot, "app", "src", sourceSet, "res", "xml", "network_security_config.xml"),
        "utf8",
      );
    const production = read("main");

    assert.match(production, /<base-config\s+cleartextTrafficPermitted="false">/);
    assert.doesNotMatch(production, /cleartextTrafficPermitted="true"/);
    assert.doesNotMatch(production, /(?:127\.0\.0\.1|localhost|10\.0\.2\.2)/);

    for (const sourceSet of ["debug", "debugOptimized"]) {
      const development = read(sourceSet);
      assert.match(development, /<base-config\s+cleartextTrafficPermitted="false">/);
      assert.match(development, /<domain\s+includeSubdomains="false">10\.0\.2\.2<\/domain>/);
      assert.match(development, /<domain\s+includeSubdomains="false">127\.0\.0\.1<\/domain>/);
      assert.match(development, /<domain\s+includeSubdomains="false">localhost<\/domain>/);
    }
  } finally {
    fs.rmSync(platformProjectRoot, { recursive: true, force: true });
  }
}

main()
  .then(() => console.log("[pass] Expo prebuild source-set network security remains production fail-closed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
