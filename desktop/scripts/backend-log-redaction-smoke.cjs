const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");

const originalLoad = Module._load;
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "lengrvis-backend-log-redaction-"));
const fakeSecret = (...parts) => parts.join("");

Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "electron") {
    return {
      app: {
        getAppPath: () => tmpRoot,
        getPath: () => tmpRoot,
        isPackaged: false
      }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const rawSecrets = [
  fakeSecret("sk", "-test", "-1234567890abcdef"),
  fakeSecret("desktop", "-token", "-1234567890"),
  fakeSecret("bearer", "-secret", "-1234567890"),
  fakeSecret("cookie", "-secret", "-1234567890"),
  fakeSecret("url", "-token", "-1234567890"),
  fakeSecret("oauth", "-code", "-1234567890"),
  fakeSecret("client", "-secret", "-1234567890"),
  fakeSecret("api", "-secret", "-1234567890"),
  fakeSecret("desktop", "-header", "-secret", "-1234567890")
];

function assertNoRawSecrets(text, label) {
  for (const secret of rawSecrets) {
    assert.equal(text.includes(secret), false, `${label} must redact ${secret}`);
  }
}

(async () => {
  try {
    const { redactBackendLogText, writeBackendLog } = require("../dist/main/backendProcess.js");
    const sample = [
      `args=["--api-key","${rawSecrets[0]}","--desktop-token","${rawSecrets[1]}"]`,
      `Authorization: Bearer ${rawSecrets[2]}`,
      `Cookie: session=${rawSecrets[3]}; theme=dark`,
      `url=https://example.test/callback?token=${rawSecrets[4]}&safe=1&code=${rawSecrets[5]}&client_secret=${rawSecrets[6]}`,
      `api_key=${rawSecrets[7]}`,
      `X-Lengrvis-Desktop-Token=${rawSecrets[8]}`
    ].join(" ");

    const redacted = redactBackendLogText(sample);
    assertNoRawSecrets(redacted, "redaction result");
    assert.match(redacted, /\[redacted\]|%5Bredacted%5D/);

    await writeBackendLog(sample);
    const logText = fs.readFileSync(path.join(tmpRoot, "backend-process.log"), "utf8");
    assertNoRawSecrets(logText, "backend-process.log");
    assert.match(logText, /\[redacted\]|%5Bredacted%5D/);

    console.log("backend process log redaction smoke passed");
  } finally {
    Module._load = originalLoad;
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
