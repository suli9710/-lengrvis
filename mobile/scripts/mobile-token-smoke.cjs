const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");

const clientSource = readFileSync(path.resolve(__dirname, "../src/api/client.ts"), "utf8");

assert.match(clientSource, /const MOBILE_AUTH_WS_PROTOCOL_PREFIX = "lengrvis\.mobile\.token\."/);
assert.doesNotMatch(
  clientSource,
  /url\.searchParams\.set\("token"/,
  "mobile WebSocket helpers must not put bearer tokens in URL query strings"
);
assert.match(
  clientSource,
  /return \[`\$\{MOBILE_AUTH_WS_PROTOCOL_PREFIX\}\$\{session\.token\}`\];/,
  "mobile WebSocket helpers should pass bearer tokens via a subprotocol"
);

console.log("Mobile token smoke passed");

