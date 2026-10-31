const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../src/state/MobileCompanionContext.tsx"), "utf8");
const tree = ts.createSourceFile("companion.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let effect;
function walk(node) {
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect" &&
      node.arguments[0]?.getText(tree).includes("let closedByEffect = false")) effect = node.arguments[0].getText(tree);
  ts.forEachChild(node, walk);
}
walk(tree);
assert.ok(effect, "approval stream lifecycle effect exists");
const compiled = ts.transpileModule(`(${effect})`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;

async function run() {
  const calls = [];
  const sockets = [];
  let rejectRefresh;
  const refresh = new Promise((_, reject) => { rejectRefresh = reject; });
  class AuthExpiredError extends Error {}
  const context = {
    AuthExpiredError,
    reconnectTimerRef: { current: null }, reconnectAttemptRef: { current: 0 }, socketRef: { current: null },
    session: { deviceId: "device" }, clearTimeout,
    refreshAllAsync: () => refresh,
    approvalWebSocketConnectionInfo: () => ({ url: "wss://example.invalid", protocols: [] }),
    WebSocket: class { constructor() { sockets.push(this); } close() {} },
    isRemoteInputGrantUsable: () => true,
    errorMessage: () => "error",
  };
  for (const name of ["setConnection", "setError", "setHasLoadedOnce", "handleAuthExpired", "mergePendingApprovals", "setTasks", "onRemoteInputGrant", "onRemoteInputGrantRevoked", "upsertApproval", "scheduleReconnect"]) {
    context[name] = (...args) => calls.push([name, ...args]);
  }
  const start = vm.runInNewContext(compiled, context);
  const cleanup = start();
  const socket = sockets[0];
  socket.onopen();
  assert.ok(calls.some(([name, state]) => name === "setConnection" && state === "online"));
  cleanup();
  calls.length = 0;
  socket.onopen();
  socket.onmessage({ data: JSON.stringify({ type: "mobile_device_revoked", device_id: "device" }) });
  socket.onerror();
  socket.onclose({ code: 1008 });
  rejectRefresh(new AuthExpiredError());
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [], "retired stream and refresh callbacks must not expire or mutate the replacement session");

  const nextCleanup = start();
  sockets[1].onclose({ code: 1008 });
  assert.ok(calls.some(([name]) => name === "handleAuthExpired"), "current stream still handles revocation");
  nextCleanup();
  console.log("Companion stream lifecycle smoke passed");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
