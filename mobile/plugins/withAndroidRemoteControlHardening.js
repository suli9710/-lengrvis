const { AndroidConfig, withAndroidManifest, withDangerousMod, withMainActivity } = require("@expo/config-plugins");
const fs = require("fs");
const path = require("path");

const NETWORK_SECURITY_CONFIG = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="false">
    <trust-anchors>
      <certificates src="system" />
      <certificates src="user" />
    </trust-anchors>
  </base-config>
</network-security-config>
`;

function withAndroidNetworkSecurityConfig(config) {
  config = withAndroidManifest(config, (modConfig) => {
    const mainApplication = AndroidConfig.Manifest.getMainApplicationOrThrow(modConfig.modResults);
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
  if (source.includes("WindowManager.LayoutParams.FLAG_SECURE")) {
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
