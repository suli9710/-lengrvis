const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const sourcePath = path.resolve(__dirname, "../src/remoteInputGrant.ts");
const clientSourcePath = path.resolve(__dirname, "../src/api/client.ts");
const approvalsSourcePath = path.resolve(__dirname, "../src/screens/ApprovalsScreen.tsx");
const remoteScreenSourcePath = path.resolve(__dirname, "../src/screens/RemoteScreen.tsx");
const notificationsSourcePath = path.resolve(__dirname, "../src/notifications.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const clientSource = fs.readFileSync(clientSourcePath, "utf8");
const approvalsSource = fs.readFileSync(approvalsSourcePath, "utf8");
const remoteScreenSource = fs.readFileSync(remoteScreenSourcePath, "utf8");
const notificationsSource = fs.readFileSync(notificationsSourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    strict: true,
  },
}).outputText;

const sandbox = {
  exports: {},
  module: { exports: {} },
  require,
};
sandbox.exports = sandbox.module.exports;
vm.runInNewContext(compiled, sandbox, { filename: sourcePath });

const {
  isRemoteInputGrantUsable,
  mapViewerPointToRemote,
  reduceRemoteInputGrant,
  remoteInputGrantExpiryDelayMs,
  remoteInputGrantRemainingText,
} = sandbox.module.exports;
const now = Date.parse("2026-06-01T00:00:00.000Z");
const activeGrant = {
  id: "rig_active",
  status: "active",
  scope: "remote:input",
  created_at: "2026-06-01T00:00:00.000Z",
  expires_at: "2026-06-01T00:05:00.000Z",
};

assert.equal(isRemoteInputGrantUsable(activeGrant, now), true);
assert.equal(remoteInputGrantExpiryDelayMs(activeGrant, now), 300000);
assert.equal(remoteInputGrantRemainingText(activeGrant, now), "5 分 00 秒");
assert.equal(remoteInputGrantRemainingText({ ...activeGrant, revoked_at: "2026-06-01T00:01:00.000Z" }, now), "未授权");
assert.equal(remoteInputGrantRemainingText({ ...activeGrant, expires_at: "2026-05-31T23:59:59.000Z" }, now), "已过期");
assert.equal(isRemoteInputGrantUsable({ ...activeGrant, expires_at: "2026-05-31T23:59:59.000Z" }, now), false);
assert.equal(isRemoteInputGrantUsable({ ...activeGrant, status: "revoked", revoked_at: "2026-06-01T00:01:00.000Z" }, now), false);
assert.equal(isRemoteInputGrantUsable({ ...activeGrant, expires_at: "" }, now), false);
assert.equal(isRemoteInputGrantUsable(null, now), false);
const nextGrant = { ...activeGrant, id: "rig_next", expires_at: "2026-06-01T00:06:00.000Z" };
assert.equal(reduceRemoteInputGrant(null, { type: "received", grant: activeGrant }, now), activeGrant);
assert.equal(reduceRemoteInputGrant(activeGrant, { type: "received", grant: nextGrant }, now), nextGrant);
assert.equal(reduceRemoteInputGrant(activeGrant, { type: "received", grant: { ...activeGrant, status: "revoked" } }, now), null);
assert.equal(reduceRemoteInputGrant(activeGrant, { type: "received", grant: { ...nextGrant, status: "revoked" } }, now), activeGrant);
assert.equal(reduceRemoteInputGrant(activeGrant, { type: "revoked", grantId: "rig_active" }, now), null);
assert.equal(reduceRemoteInputGrant(activeGrant, { type: "revoked", grantId: "rig_other" }, now), activeGrant);
assert.equal(reduceRemoteInputGrant(activeGrant, { type: "expired", grantId: "rig_active" }, now), null);
assert.equal(reduceRemoteInputGrant(activeGrant, { type: "expired", grantId: "rig_other" }, now), activeGrant);
assert.equal(reduceRemoteInputGrant(activeGrant, { type: "cleared" }, now), null);
const remoteFrame = { width: 800, height: 450, originalWidth: 1600, originalHeight: 900 };
assert.equal(JSON.stringify(mapViewerPointToRemote(400, 225, { width: 800, height: 450 }, remoteFrame)), JSON.stringify({ x: 800, y: 450 }));
assert.equal(JSON.stringify(mapViewerPointToRemote(800, 450, { width: 800, height: 450 }, remoteFrame)), JSON.stringify({ x: 1599, y: 899 }));
assert.equal(JSON.stringify(mapViewerPointToRemote(100, 225, { width: 1000, height: 450 }, remoteFrame)), JSON.stringify({ x: 0, y: 450 }));
assert.equal(mapViewerPointToRemote(99, 225, { width: 1000, height: 450 }, remoteFrame), null);
assert.equal(mapViewerPointToRemote(400, 225, { width: 0, height: 450 }, remoteFrame), null);
assert.match(clientSource, /remote_input_grant_revoked/);
assert.match(approvalsSource, /onRemoteInputGrantRevoked\(payload\.grant\)/);
assert.match(clientSource, /mobile_device_revoked/);
assert.match(approvalsSource, /payload\.type === "mobile_device_revoked"/);
assert.match(fs.readFileSync(path.resolve(__dirname, "../App.tsx"), "utf8"), /reduceRemoteInputGrant\(current, \{ type: "expired", grantId \}\)/);
assert.match(remoteScreenSource, /const resetInputConnection = useCallback/);
assert.match(remoteScreenSource, /if \(grantUsable\) void connectInput\(\);/);
assert.match(remoteScreenSource, /setConnection\("paused"\);\s*resetInputConnection\(\);\s*closeSocket\(\);\s*closeInputSocket\(\);/s);
assert.match(remoteScreenSource, /const inputConnectionGenerationRef = useRef\(0\);/);
assert.match(remoteScreenSource, /inputConnectionGenerationRef\.current \+= 1;/);
assert.match(remoteScreenSource, /connectionGeneration !== inputConnectionGenerationRef\.current \|\| !isRemoteInputGrantUsable\(effectiveGrant\)/);
assert.match(remoteScreenSource, /connectionGeneration !== inputConnectionGenerationRef\.current[\s\S]*?socket\.close\(\);[\s\S]*?return;/);
assert.match(remoteScreenSource, /catch \(currentError\) \{\s*if \(connectionGeneration !== inputConnectionGenerationRef\.current\) \{\s*return;\s*\}/);
assert.match(clientSource, /export async function revokeRemoteInputGrant/);
assert.match(clientSource, /\/api\/mobile\/remote-input-grants\/\$\{encodeURIComponent\(grantId\)\}/);
assert.match(remoteScreenSource, /remoteInputGrantRemainingText\(effectiveGrant, nowMs\)/);
assert.match(remoteScreenSource, /locallyRevokedGrantId/);
assert.match(remoteScreenSource, /onRemoteInputGrantRevoked\(revokedGrant\)/);
assert.match(remoteScreenSource, /结束接管/);
assert.doesNotMatch(notificationsSource, /body:\s*approval\.message/);
assert.match(notificationsSource, /body:\s*"有任务等待审批，打开 App 查看详情。"/);
const closeInputSocketBlock = remoteScreenSource.slice(
  remoteScreenSource.indexOf("const closeInputSocket = useCallback"),
  remoteScreenSource.indexOf("const resetInputConnection = useCallback"),
);
assert.ok(closeInputSocketBlock.includes("inputSocketRef.current?.close();"));
assert.doesNotMatch(closeInputSocketBlock, /setInputConnection/);

console.log("remote input grant smoke passed");
