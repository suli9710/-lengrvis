const assert = require("node:assert/strict");
const fs = require("node:fs");

const { loadTsModule, mobilePath } = require("./behavior-smoke-helpers.cjs");

const lifecycle = loadTsModule(mobilePath("src/sessionLifecycle.ts"));
const now = Date.parse("2026-07-10T00:00:00.000Z");

assert.equal(
  lifecycle.sessionRefreshDelayMs({ token: "token", expiresAt: "2026-07-10T00:30:00.000Z" }, now),
  25 * 60 * 1000,
  "short-lived access sessions refresh five minutes before expiry",
);
assert.equal(
  lifecycle.sessionRefreshDelayMs({ token: "token", expiresAt: "2026-07-10T00:04:00.000Z" }, now),
  0,
  "sessions inside the refresh window refresh immediately",
);
assert.equal(lifecycle.sessionRefreshDelayMs({ token: "token", expiresAt: "2026-07-09T00:00:00.000Z" }, now), null);
assert.equal(lifecycle.sessionRefreshDelayMs({ token: "", expiresAt: "2026-07-12T00:00:00.000Z" }, now), null);
assert.equal(lifecycle.sessionRefreshRetryDelayMs(0), 60_000);
assert.equal(lifecycle.sessionRefreshRetryDelayMs(10), 15 * 60_000);
assert.equal(lifecycle.mobileSessionTransition("active", "inactive"), "lock");
assert.equal(lifecycle.mobileSessionTransition("active", "background"), "lock");
assert.equal(lifecycle.mobileSessionTransition("inactive", "background"), "none");
assert.equal(lifecycle.mobileSessionTransition("background", "active"), "unlock");
assert.equal(lifecycle.mobileSessionTransition(null, "active"), "unlock");
assert.equal(lifecycle.mobileSessionTransition("active", "active"), "none");

const layoutSource = fs.readFileSync(mobilePath("app/_layout.tsx"), "utf8");
const loadEffect = layoutSource.match(/useEffect\(\(\) => \{[\s\S]*?void loadSession\(\)[\s\S]*?\n  \}, \[([^\]]*)\]\);/);
assert.ok(loadEffect, "root layout must keep an explicit session-load effect");
assert.doesNotMatch(loadEffect[1], /pathname/, "route navigation must not reload the stored session");
assert.match(
  loadEffect[0],
  /if \(stored\) \{\s*const refreshed = await refreshMobileSession\(stored\)/,
  "startup and foreground unlock must rotate every persisted session after authenticated storage access",
);
assert.match(layoutSource, /refreshMobileSession\(baseSession\)/, "root layout must refresh a paired token before expiry");
assert.match(layoutSource, /replaceSessionIfTokenMatches/, "late refreshes must use compare-and-swap persistence");
assert.match(
  loadEffect[0],
  /if \(!replaced\) \{\s*setSessionLoadAttempt\(\(attempt\) => attempt \+ 1\);\s*return;\s*\}/,
  "an initial compare-and-swap conflict must reload the newer stored session instead of staying in loading",
);
assert.match(
  layoutSource,
  /if \(transition === "lock"\) \{\s*sessionLockEpochRef\.current \+= 1;\s*resetShellState\(\);\s*setSession\(null\);\s*setSessionLoadState\("loading"\);\s*setSessionLocked\(true\);/,
  "leaving the active state must clear grants and the in-memory session before showing a locked gate",
);
assert.match(
  layoutSource,
  /if \(transition === "unlock"\) \{\s*resetShellState\(\);\s*setSession\(null\);\s*setSessionLoadState\("loading"\);\s*setSessionLocked\(false\);\s*setSessionLoadAttempt\(\(attempt\) => attempt \+ 1\);/,
  "foreground unlock must re-read authenticated storage instead of reviving the old in-memory token",
);
assert.match(
  layoutSource,
  /if \(!replaced\) \{\s*setSession\(null\);\s*setSessionLoadState\("loading"\);\s*setSessionLoadAttempt\(\(attempt\) => attempt \+ 1\);/,
  "a scheduled-refresh CAS conflict must fail closed and reload authenticated storage",
);
assert.match(layoutSource, /sessionLockEpochRef\.current === lockEpoch/, "late async work must not cross a background lock boundary");
assert.match(
  layoutSource,
  /if \(appStateRef\.current !== "active" \|\| sessionLockEpochRef\.current !== callbackLockEpoch\)/,
  "a pairing callback that completes after a lock boundary must not remount its in-memory session",
);
assert.match(layoutSource, /if \(sessionLocked\) \{[\s\S]*?locked[\s\S]*?\n  \}/, "the locked gate must win over late async state updates");
assert.match(layoutSource, /setSessionRefreshAttempt\(0\)/, "a successful refresh must reset retry backoff for the next token lifetime");

console.log("Mobile session lifecycle smoke passed");
