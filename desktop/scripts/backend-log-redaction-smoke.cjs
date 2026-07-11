const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");

const originalLoad = Module._load;
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "lengrvis-backend-log-redaction-"));
const fakeSecret = (...parts) => parts.join("");
const MAX_EXPECTED_LOG_FILE_BYTES = 512 * 1024;
const MAX_EXPECTED_LOG_ENTRY_BYTES = 64 * 1024;

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

    await writeBackendLog(`oversized-entry ${"x".repeat(MAX_EXPECTED_LOG_ENTRY_BYTES * 2)}`);
    const truncatedLog = fs.readFileSync(path.join(tmpRoot, "backend-process.log"), "utf8");
    assert.ok(
      Buffer.byteLength(truncatedLog, "utf8") <= MAX_EXPECTED_LOG_ENTRY_BYTES + 1024,
      "an oversized backend log entry must be truncated"
    );
    assert.match(truncatedLog, /\[truncated\]/, "truncated backend log entries must be marked");

    for (let index = 0; index < 16; index += 1) {
      await writeBackendLog(`rotation-marker-${index} ${"x".repeat(MAX_EXPECTED_LOG_ENTRY_BYTES)}`);
    }
    const activeLogPath = path.join(tmpRoot, "backend-process.log");
    const activeLog = fs.readFileSync(activeLogPath, "utf8");
    assert.ok(
      Buffer.byteLength(activeLog, "utf8") <= MAX_EXPECTED_LOG_FILE_BYTES + 1024,
      "the active backend log must remain bounded after rotation"
    );
    assert.equal(activeLog.includes("rotation-marker-0"), false, "rotation must retire the oldest active log entries");
    assert.equal(activeLog.includes("rotation-marker-15"), true, "rotation must retain the latest log entry");
    assert.equal(fs.existsSync(`${activeLogPath}.1`), true, "rotation must keep one previous log file");

    console.log("backend process log redaction smoke passed");
  } finally {
    Module._load = originalLoad;
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
