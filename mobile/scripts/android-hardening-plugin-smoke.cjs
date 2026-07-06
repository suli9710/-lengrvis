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
