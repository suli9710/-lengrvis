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

  // Inner sub-screens return to the approvals list (mirrors their in-app back).
  for (const screen of ["remote", "wakeups"]) {
    assert.equal(
      resolveAndroidBack({ sessionActive: true, activeScreen: screen, hasSelectedApproval: false }),
      "return_to_approvals",
      `back from ${screen} must return to approvals`,
    );
    // Sub-screens take precedence over an open approval detail (render order).
    assert.equal(
      resolveAndroidBack({ sessionActive: true, activeScreen: screen, hasSelectedApproval: true }),
      "return_to_approvals",
      `${screen} must outrank an open approval detail`,
    );
  }

  // An open approval detail closes back to the list.
  assert.equal(
    resolveAndroidBack({ sessionActive: true, activeScreen: "approvals", hasSelectedApproval: true }),
    "close_approval_detail",
  );

  // The approvals list root exits the app via the OS default.
  assert.equal(
    resolveAndroidBack({ sessionActive: true, activeScreen: "approvals", hasSelectedApproval: false }),
    "exit_app",
  );

  assert.equal(androidBackIsHandled("return_to_approvals"), true);
  assert.equal(androidBackIsHandled("close_approval_detail"), true);
  assert.equal(androidBackIsHandled("exit_app"), false);
}

function assertAppWiresBackHandler() {
  const source = fs.readFileSync(mobilePath("App.tsx"), "utf8");
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
