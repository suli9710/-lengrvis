const { AndroidConfig, withAndroidManifest, withDangerousMod, withMainActivity } = require("@expo/config-plugins");
const fs = require("fs");
const path = require("path");

// Production must never inherit loopback cleartext exceptions. The app can
// still pair with a local desktop over HTTPS; insecure development transports
// live only in the debug source sets below.
// User-installed CA trust is intentionally omitted; only system trust anchors
// are accepted to reduce MITM risk.
const PRODUCTION_NETWORK_SECURITY_CONFIG = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="false">
    <trust-anchors>
      <certificates src="system" />
    </trust-anchors>
  </base-config>
</network-security-config>
`;

const DEVELOPMENT_NETWORK_SECURITY_CONFIG = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="false">
    <trust-anchors>
      <certificates src="system" />
    </trust-anchors>
  </base-config>
  <domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="false">10.0.2.2</domain>
    <domain includeSubdomains="false">127.0.0.1</domain>
    <domain includeSubdomains="false">localhost</domain>
  </domain-config>
</network-security-config>
`;

// P1-17 fix: Use regex to detect an actual setFlags call with FLAG_SECURE,
// not just a string mention (which could be in a comment or import).
const FLAG_SECURE_CALL_RE = /\b(?:window|getWindow\(\))\.setFlags\s*\([^)]*FLAG_SECURE[^)]*\)/;
const BLOCKED_ANDROID_PERMISSIONS = [
  "android.permission.READ_EXTERNAL_STORAGE",
  "android.permission.SYSTEM_ALERT_WINDOW",
  "android.permission.WRITE_EXTERNAL_STORAGE",
];

function withAndroidNetworkSecurityConfig(config) {
  config = withAndroidManifest(config, (modConfig) => {
    AndroidConfig.Permissions.removePermissions(modConfig.modResults, BLOCKED_ANDROID_PERMISSIONS);
    const mainApplication = AndroidConfig.Manifest.getMainApplicationOrThrow(modConfig.modResults);
    mainApplication.$["android:allowBackup"] = "false";
    mainApplication.$["android:networkSecurityConfig"] = "@xml/network_security_config";
    mainApplication.$["android:usesCleartextTraffic"] = "false";
    return modConfig;
  });

  return withDangerousMod(config, [
    "android",
    async (modConfig) => {
      await writeNetworkSecurityConfigs(modConfig.modRequest.platformProjectRoot);
      return modConfig;
    },
  ]);
}

async function writeNetworkSecurityConfigs(platformProjectRoot) {
  const sourceSetConfigs = [
    ["main", PRODUCTION_NETWORK_SECURITY_CONFIG],
    ["debug", DEVELOPMENT_NETWORK_SECURITY_CONFIG],
    ["debugOptimized", DEVELOPMENT_NETWORK_SECURITY_CONFIG],
  ];

  await Promise.all(
    sourceSetConfigs.map(async ([sourceSet, content]) => {
      const xmlDir = path.join(platformProjectRoot, "app", "src", sourceSet, "res", "xml");
      await fs.promises.mkdir(xmlDir, { recursive: true });
      await fs.promises.writeFile(path.join(xmlDir, "network_security_config.xml"), content, "utf8");
    }),
  );
}

function addFlagSecure(source, language) {
  // P1-17 fix: Use regex to check for an actual setFlags(FLAG_SECURE) call
  // instead of a simple string includes check that can be bypassed by
  // comments or import statements containing 'FLAG_SECURE'.
  if (FLAG_SECURE_CALL_RE.test(source)) {
    return source;
  }

  if (language === "kt") {
    let next = source;
    if (!next.includes("import android.view.WindowManager")) {
      next = next.replace(/(package\s+[^\n]+\n)/, "$1\nimport android.view.WindowManager\n");
    }
    const updated = next.replace(
      /(super\.onCreate\([^)]*\)\s*)/,
      "$1\n    window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)\n",
    );
    if (updated === next) {
      throw new Error("Unable to inject FLAG_SECURE: Kotlin MainActivity has no super.onCreate(...) call");
    }
    return updated;
  }

  let next = source;
  if (!next.includes("import android.view.WindowManager;")) {
    next = next.replace(/(package\s+[^;]+;\s*)/, "$1\nimport android.view.WindowManager;\n");
  }
  const updated = next.replace(
    /(super\.onCreate\([^)]*\);\s*)/,
    "$1\n    getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);\n",
  );
  if (updated === next) {
    throw new Error("Unable to inject FLAG_SECURE: Java MainActivity has no super.onCreate(...) call");
  }
  return updated;
}

function withAndroidFlagSecure(config) {
  return withMainActivity(config, (modConfig) => {
    modConfig.modResults.contents = addFlagSecure(
      modConfig.modResults.contents,
      modConfig.modResults.language,
    );
    return modConfig;
  });
}

function withAndroidRemoteControlHardening(config) {
  config = withAndroidNetworkSecurityConfig(config);
  return withAndroidFlagSecure(config);
}

module.exports = withAndroidRemoteControlHardening;
module.exports.addFlagSecure = addFlagSecure;
module.exports.writeNetworkSecurityConfigs = writeNetworkSecurityConfigs;
