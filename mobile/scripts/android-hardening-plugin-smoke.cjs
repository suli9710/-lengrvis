const assert = require("node:assert/strict");

const { addFlagSecure } = require("../plugins/withAndroidRemoteControlHardening");

const kotlinNullActivity = `package com.lengrvis

class MainActivity : ReactActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(null)
  }
}
`;

const kotlinSavedStateActivity = `package com.lengrvis

class MainActivity : ReactActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
  }
}
`;

const javaNullActivity = `package com.lengrvis;

class MainActivity extends ReactActivity {
  @Override
  protected void onCreate(Bundle savedInstanceState) {
    super.onCreate(null);
  }
}
`;

const javaSavedStateActivity = `package com.lengrvis;

class MainActivity extends ReactActivity {
  @Override
  protected void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
  }
}
`;

for (const [name, source, language] of [
  ["kotlin null", kotlinNullActivity, "kt"],
  ["kotlin savedInstanceState", kotlinSavedStateActivity, "kt"],
  ["java null", javaNullActivity, "java"],
  ["java savedInstanceState", javaSavedStateActivity, "java"],
]) {
  const updated = addFlagSecure(source, language);
  assert.match(updated, /WindowManager/, `${name} should import WindowManager`);
  assert.match(updated, /FLAG_SECURE/, `${name} should inject FLAG_SECURE`);
  assert.match(updated, /setFlags\([^)]*FLAG_SECURE[^)]*\)/, `${name} should contain a real FLAG_SECURE setFlags call`);
}

assert.throws(
  () => addFlagSecure("package com.lengrvis\n\nclass MainActivity : ReactActivity() {}\n", "kt"),
  /no super\.onCreate\(\.\.\.\) call/,
  "Kotlin MainActivity without super.onCreate should fail the build",
);

assert.throws(
  () => addFlagSecure("package com.lengrvis;\n\nclass MainActivity extends ReactActivity {}\n", "java"),
  /no super\.onCreate\(\.\.\.\) call/,
  "Java MainActivity without super.onCreate should fail the build",
);

console.log("[pass] Android hardening plugin injects FLAG_SECURE and fails closed");

// Keep the checked-in native project and regenerated Expo project compatible
// with AGP 9 without bypassing Kotlin compilation or security hardening.
const fs = require("node:fs");
const path = require("node:path");
const { applyAndroidBuildCompatibility } = require("../plugins/withAndroidBuildCompatibility");
const preserved = [
  { type: "comment", value: "Keep hardening" },
  { type: "property", key: "android.enableMinifyInReleaseBuilds", value: "true" },
  { type: "property", key: "android.enableShrinkResourcesInReleaseBuilds", value: "true" },
];
for (const input of [[], preserved, [
  ...preserved,
  { type: "property", key: "android.builtInKotlin", value: "true" },
  { type: "property", key: "android.builtInKotlin", value: "false" },
  { type: "property", key: "android.newDsl", value: "true" },
  { type: "property", key: "android.cmakeVersion", value: "3.22.1" },
  { type: "property", key: "android.cmakeVersion", value: "3.31.6" },
]]) {
  const snapshot = JSON.stringify(input);
  const output = applyAndroidBuildCompatibility(input);
  assert.equal(JSON.stringify(input), snapshot, "plugin must not mutate input");
  for (const [key, value] of [["android.builtInKotlin", "false"], ["android.newDsl", "false"], ["android.cmakeVersion", "3.31.6"]]) {
    assert.deepEqual(output.filter((item) => item.type === "property" && item.key === key), [
      { type: "property", key, value },
    ]);
  }
  for (const item of input.filter((entry) => preserved.includes(entry))) assert(output.includes(item));
  assert.deepEqual(applyAndroidBuildCompatibility(output), output, "prebuild must be idempotent");
}
const mobileRoot = path.join(__dirname, "..");
const appConfig = JSON.parse(fs.readFileSync(path.join(mobileRoot, "app.json"), "utf8"));
assert(appConfig.expo.plugins.includes("./plugins/withAndroidBuildCompatibility"));
const properties = fs.readFileSync(path.join(mobileRoot, "android", "gradle.properties"), "utf8");
for (const [key, value] of [["android.builtInKotlin", "false"], ["android.newDsl", "false"], ["android.cmakeVersion", "3.31.6"]]) {
  const values = properties.split(/\r?\n/).filter((line) => line.startsWith(`${key}=`));
  assert.deepEqual(values, [`${key}=${value}`]);
}
const appGradle = fs.readFileSync(path.join(mobileRoot, "android", "app", "build.gradle"), "utf8");
assert(appGradle.includes('apply plugin: "org.jetbrains.kotlin.android"'));
assert(appGradle.includes('getDefaultProguardFile("proguard-android-optimize.txt")'));
assert(!appGradle.includes('getDefaultProguardFile("proguard-android.txt")'));
assert(properties.includes("android.enableMinifyInReleaseBuilds=true"));
assert(properties.includes("android.enableShrinkResourcesInReleaseBuilds=true"));
console.log("[pass] AGP 9 Kotlin/DSL compatibility survives prebuild without disabling release optimization");

async function verifyRegisteredGradleMod() {
  const plugin = require("../plugins/withAndroidBuildCompatibility");
  const config = plugin({ name: "compat-smoke", slug: "compat-smoke" });
  const result = await config.mods.android.gradleProperties({
    ...config,
    modResults: preserved,
    modRequest: { platform: "android", modName: "gradleProperties", projectRoot: mobileRoot },
  });
  assert.deepEqual(result.modResults, applyAndroidBuildCompatibility(preserved));
  console.log("[pass] registered Expo gradleProperties mod applies the compatibility pair");
}
verifyRegisteredGradleMod().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

const { verifyNativeToolIdentity } = require("./verify-native-tool-identity.cjs");
const expectedTool = "C:\\Android\\android-sdk\\cmake\\3.31.6\\bin\\ninja.exe";
const canonicalTool = "C:\\Program Files (x86)\\Android\\android-sdk\\cmake\\3.31.6\\bin\\ninja.exe";
const identityIo = {
  platform: "win32",
  realpath: (file) => file === expectedTool ? canonicalTool : file,
  stat: () => ({ isFile: () => true, dev: 1n, ino: 2n }),
  readFile: () => Buffer.from("verified SDK Ninja"),
};
assert.equal(verifyNativeToolIdentity(expectedTool, canonicalTool, identityIo).canonicalPath, canonicalTool);
assert.equal(verifyNativeToolIdentity(expectedTool, canonicalTool.toUpperCase(), identityIo).sha256.length, 64);
assert.throws(() => verifyNativeToolIdentity(expectedTool, "C:\\other\\ninja.exe", identityIo), /Unexpected native generator/,
  "An arbitrary executable with identical bytes must not qualify as the installed SDK tool");
assert.throws(() => verifyNativeToolIdentity(expectedTool, canonicalTool, {
  ...identityIo, realpath: () => { throw new Error("ENOENT"); },
}), /ENOENT/);
assert.throws(() => verifyNativeToolIdentity(expectedTool, canonicalTool, {
  ...identityIo, stat: () => ({ isFile: () => false }),
}), /regular file/);
let toolReads = 0;
assert.throws(() => verifyNativeToolIdentity(expectedTool, canonicalTool, {
  ...identityIo, readFile: () => Buffer.from(`changed-${toolReads++}`),
}), /content changed/);
assert.throws(() => verifyNativeToolIdentity("/sdk/Ninja", "/sdk/ninja", {
  ...identityIo, platform: "linux", realpath: (file) => file,
}), /Unexpected native generator/, "POSIX case-distinct paths remain distinct");
console.log("[pass] native tool identity accepts SDK aliases and rejects different/missing/changed executables");

const identityFixture = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "lengrvis-native-tool-"));
try {
  const sdk = path.join(identityFixture, "sdk");
  const alias = path.join(identityFixture, "sdk-alias");
  fs.mkdirSync(sdk);
  const tool = path.join(sdk, "ninja.exe");
  fs.writeFileSync(tool, "fixture executable bytes");
  fs.symlinkSync(sdk, alias, "junction");
  assert.equal(verifyNativeToolIdentity(tool, path.join(alias, "ninja.exe")).canonicalPath, fs.realpathSync.native(tool));
  const otherTool = path.join(identityFixture, "ninja.exe");
  fs.copyFileSync(tool, otherTool);
  assert.throws(() => verifyNativeToolIdentity(tool, otherTool), /Unexpected native generator/);
} finally {
  fs.rmSync(identityFixture, { recursive: true, force: true });
}
let identityStats = 0;
assert.throws(() => verifyNativeToolIdentity(expectedTool, canonicalTool, {
  ...identityIo, stat: () => ({ isFile: () => true, dev: 1n, ino: BigInt(identityStats++) }),
}), /file identity differs/);
console.log("[pass] actual filesystem junction aliases resolve to the same native tool");
