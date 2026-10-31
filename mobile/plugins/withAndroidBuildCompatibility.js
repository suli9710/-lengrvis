const { withGradleProperties } = require("@expo/config-plugins");

// Expo SDK 58's native template keeps the Kotlin Android plugin and legacy DSL.
// AGP 9 supports these explicit opt-outs; AGP 10 requires a real migration.
// https://docs.expo.dev/bare/upgrade/
// https://developer.android.com/build/migrate-to-built-in-kotlin
const COMPATIBILITY_PROPERTIES = ["android.builtInKotlin", "android.newDsl"];

function applyAndroidBuildCompatibility(properties) {
  // Remove duplicate/conflicting entries so prebuild is deterministic/idempotent.
  return [
    ...properties.filter((item) => item.type !== "property" || !COMPATIBILITY_PROPERTIES.includes(item.key)),
    ...COMPATIBILITY_PROPERTIES.map((key) => ({ type: "property", key, value: "false" })),
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
