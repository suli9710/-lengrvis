const assert = require("node:assert/strict");
const fs = require("node:fs");

const { loadTsModule, mobilePath } = require("./behavior-smoke-helpers.cjs");

function assertResolverDecisionTable(nav) {
  const { resolveAndroidBack, androidBackIsHandled } = nav;

  // Before pairing (PairScreen / session-load screens) back falls through to the
  // OS so the user can leave the app normally.
  assert.equal(
    resolveAndroidBack({ sessionActive: false, activeScreen: "approvals", hasSelectedApproval: false }),
    "exit_app",
  );
  assert.equal(
    resolveAndroidBack({ sessionActive: false, activeScreen: "remote", hasSelectedApproval: true }),
    "exit_app",
  );

  // Tab sub-screens return to the Home tab.
  for (const screen of ["approvals", "remote", "wakeups"]) {
    assert.equal(
      resolveAndroidBack({ sessionActive: true, route: { kind: "tab", tab: screen } }),
      "return_to_home",
      `back from ${screen} must return to home`,
    );
  }

  // An open approval detail closes back to the previous route.
  assert.equal(
    resolveAndroidBack({ sessionActive: true, route: { kind: "approvalDetail" } }),
    "go_back",
  );

  // The home root exits the app via the OS default.
  assert.equal(
    resolveAndroidBack({ sessionActive: true, route: { kind: "tab", tab: "home" } }),
    "exit_app",
  );

  assert.equal(androidBackIsHandled("return_to_home"), true);
  assert.equal(androidBackIsHandled("go_back"), true);
  assert.equal(androidBackIsHandled("exit_app"), false);
}

function assertAppWiresBackHandler() {
  const source = fs.readFileSync(mobilePath("app/_layout.tsx"), "utf8");
  // The hardware back button must be wired and gated to Android, and use the
  // shared resolver instead of ad-hoc inline logic.
  assert.match(source, /resolveAndroidBack/, "App.tsx must use the shared back resolver");
  assert.match(source, /BackHandler\.addEventListener\("hardwareBackPress"/, "App.tsx must register the hardware back listener");
  assert.match(source, /Platform\.OS !== "android"/, "back handler must be gated to Android");
  assert.match(source, /subscription\.remove\(\)/, "back listener must be cleaned up");
}

function main() {
  const nav = loadTsModule(mobilePath("src/androidBackNavigation.ts"));
  assertResolverDecisionTable(nav);
  assertAppWiresBackHandler();
}

main();
console.log("android back navigation behavior smoke passed");
