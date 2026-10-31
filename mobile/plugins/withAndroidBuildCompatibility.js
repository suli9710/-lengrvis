const { withGradleProperties } = require("@expo/config-plugins");

// Expo SDK 58's native template keeps the Kotlin Android plugin and legacy DSL.
// AGP 9 supports these explicit opt-outs; AGP 10 requires a real migration.
// https://docs.expo.dev/bare/upgrade/
// https://developer.android.com/build/migrate-to-built-in-kotlin
// Expo's documented Windows fix: SDK CMake 3.31.6 ships Ninja 1.12.1.
// https://docs.expo.dev/troubleshooting/windows-long-paths/
const COMPATIBILITY_PROPERTIES = {
  "android.builtInKotlin": "false",
  "android.newDsl": "false",
  "android.cmakeVersion": "3.31.6",
};

function applyAndroidBuildCompatibility(properties) {
  // Remove duplicate/conflicting entries so prebuild is deterministic/idempotent.
  return [
    ...properties.filter((item) => item.type !== "property" || !Object.hasOwn(COMPATIBILITY_PROPERTIES, item.key)),
    ...Object.entries(COMPATIBILITY_PROPERTIES).map(([key, value]) => ({ type: "property", key, value })),
  ];
}

function withAndroidBuildCompatibility(config) {
  return withGradleProperties(config, (modConfig) => {
    modConfig.modResults = applyAndroidBuildCompatibility(modConfig.modResults);
    return modConfig;
  });
}
module.exports = withAndroidBuildCompatibility;
module.exports.applyAndroidBuildCompatibility = applyAndroidBuildCompatibility;
