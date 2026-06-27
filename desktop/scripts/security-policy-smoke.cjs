const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const desktopRoot = path.resolve(__dirname, "..");
const indexHtml = fs.readFileSync(path.join(desktopRoot, "index.html"), "utf8");
const mainTs = fs.readFileSync(path.join(desktopRoot, "src", "main", "main.ts"), "utf8");

for (const directive of [
  "base-uri 'none'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "style-src-elem 'self'",
  "style-src-attr 'unsafe-inline'"
]) {
  assert.ok(indexHtml.includes(directive), `CSP must include ${directive}`);
}
assert.equal(
  indexHtml.includes("style-src 'self' 'unsafe-inline'"),
  false,
  "CSP must not allow generic inline stylesheets"
);
assert.equal(indexHtml.includes("script-src 'self' 'unsafe-inline'"), false, "CSP must not allow inline scripts");

assert.ok(mainTs.includes("function hardenDefaultSessionPermissions"), "main window session permissions must be hardened");
assert.ok(
  mainTs.includes("session.defaultSession.setPermissionRequestHandler"),
  "defaultSession permission request handler must be configured"
);
assert.ok(
  mainTs.includes("session.defaultSession.setPermissionCheckHandler"),
  "defaultSession permission check handler must be configured"
);
assert.ok(mainTs.includes('permission === "clipboard-sanitized-write"'), "clipboard write must be explicit");
assert.ok(mainTs.includes('permission !== "media"'), "non-media Web permissions must default deny");
assert.ok(mainTs.includes('mediaTypes[0] === "audio"'), "media permission must be limited to audio");

console.log("security-policy-smoke: ok");
