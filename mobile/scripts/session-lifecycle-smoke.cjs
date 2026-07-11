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

const layoutSource = fs.readFileSync(mobilePath("app/_layout.tsx"), "utf8");
const loadEffect = layoutSource.match(/useEffect\(\(\) => \{[\s\S]*?void loadSession\(\)[\s\S]*?\n  \}, \[([^\]]*)\]\);/);
assert.ok(loadEffect, "root layout must keep an explicit session-load effect");
assert.doesNotMatch(loadEffect[1], /pathname/, "route navigation must not reload the stored session");
assert.match(layoutSource, /refreshMobileSession\(baseSession\)/, "root layout must refresh a paired token before expiry");
assert.match(layoutSource, /replaceSessionIfTokenMatches/, "late refreshes must use compare-and-swap persistence");
assert.match(layoutSource, /setSessionRefreshAttempt\(0\)/, "a successful refresh must reset retry backoff for the next token lifetime");

console.log("Mobile session lifecycle smoke passed");
