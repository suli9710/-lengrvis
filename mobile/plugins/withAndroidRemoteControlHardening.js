const { AndroidConfig, withAndroidManifest, withDangerousMod, withMainActivity } = require("@expo/config-plugins");
const fs = require("fs");
const path = require("path");

// Loopback cleartext is exempted because the API client deliberately allows
// http://127.0.0.1 / http://localhost (emulator and adb-reverse pairing flows);
// without this domain-config the release build would block that path at the
// network layer while the client UI still offers it.
// P1-14 fix: <certificates src="user" /> removed — only system trust anchors
// are accepted to prevent MITM via user-installed CA certificates.
const NETWORK_SECURITY_CONFIG = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="false">
    <trust-anchors>
      <certificates src="system" />
    </trust-anchors>
  </base-config>
  <domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="false">127.0.0.1</domain>
    <domain includeSubdomains="false">localhost</domain>
  </domain-config>
</network-security-config>
`;

// P1-17 fix: Use regex to detect an actual setFlags call with FLAG_SECURE,
// not just a string mention (which could be in a comment or import).
const FLAG_SECURE_CALL_RE = /\b(?:window|getWindow\(\))\.setFlags\s*\([^)]*FLAG_SECURE[^)]*\)/;

function withAndroidNetworkSecurityConfig(config) {
  config = withAndroidManifest(config, (modConfig) => {
    const mainApplication = AndroidConfig.Manifest.getMainApplicationOrThrow(modConfig.modResults);
    mainApplication.$["android:allowBackup"] = "false";
    mainApplication.$["android:networkSecurityConfig"] = "@xml/network_security_config";
    mainApplication.$["android:usesCleartextTraffic"] = "false";
    return modConfig;
  });

  return withDangerousMod(config, [
    "android",
    async (modConfig) => {
      const xmlDir = path.join(modConfig.modRequest.platformProjectRoot, "app", "src", "main", "res", "xml");
      await fs.promises.mkdir(xmlDir, { recursive: true });
      await fs.promises.writeFile(path.join(xmlDir, "network_security_config.xml"), NETWORK_SECURITY_CONFIG, "utf8");
      return modConfig;
    },
  ]);
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
    return next.replace(
      /(super\.onCreate\(null\)\s*)/,
      "$1\n    window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)\n",
    );
  }

  let next = source;
  if (!next.includes("import android.view.WindowManager;")) {
    next = next.replace(/(package\s+[^;]+;\s*)/, "$1\nimport android.view.WindowManager;\n");
  }
  return next.replace(
    /(super\.onCreate\(null\);\s*)/,
    "$1\n    getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);\n",
  );
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

module.exports = function withAndroidRemoteControlHardening(config) {
  config = withAndroidNetworkSecurityConfig(config);
  return withAndroidFlagSecure(config);
};
