const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");

const { loadMobileClient, loadTsModule, mobilePath } = require("./behavior-smoke-helpers.cjs");

const SESSION_KEY = "lengrvis.mobile.session";
const TOKEN_KEY = "lengrvis.mobile.session.token";
const REFRESH_TOKEN_KEY = "lengrvis.mobile.session.refresh-token";
const layoutSource = fs.readFileSync(mobilePath("app/_layout.tsx"), "utf8");
const tree = ts.createSourceFile("_layout.tsx", layoutSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const effects = [];
const callbacks = new Map();
function walk(node) {
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect") {
    effects.push(node.arguments[0].getText(tree));
  }
  if (ts.isVariableDeclaration(node) && node.initializer && ts.isCallExpression(node.initializer) &&
      node.initializer.expression.getText(tree) === "useCallback") {
    callbacks.set(node.name.getText(tree), node.initializer.arguments[0].getText(tree));
  }
  ts.forEachChild(node, walk);
}
walk(tree);
const loadEffect = effects.find((source) => source.includes("void loadAndRefreshSession()"));
const timerEffect = effects.find((source) => source.includes("const refreshDelay"));
const appStateEffect = effects.find((source) => source.includes("const handleAppStateChange"));
assert.ok(loadEffect && timerEffect && appStateEffect, "exercise the actual root session effects");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function flush() {
  // Native/storage/API boundaries each add Promise jobs; no wall-clock delay.
  for (let index = 0; index < 100; index += 1) await Promise.resolve();
}

function refreshToken(label) {
  const tokenId = crypto.createHash("sha256").update(`id:${label}`).digest("hex").slice(0, 32);
  const secret = crypto.createHash("sha256").update(`secret:${label}`).digest("base64url");
  return `lrt.mrt_${tokenId}.${secret}`;
}

function originalSession(label = "original") {
  return {
    baseUrl: "https://mobile.example.test",
    token: `test-access-${label}`,
    refreshToken: refreshToken(label),
    deviceId: "test-device",
    tokenFamilyId: `test-family-${label}`,
    deviceCredentialId: `test-credential-${label}`,
    expiresAt: new Date(Date.now() + 30 * 60_000).toISOString(),
    refreshExpiresAt: new Date(Date.now() + 30 * 86400_000).toISOString(),
  };
}

function refreshResponse(session, suffix = "next") {
  return new Response(JSON.stringify({
    token: `${session.token}-${suffix}`,
    token_type: "Bearer",
    device_id: session.deviceId,
    refresh_token: refreshToken(`${session.refreshToken}-${suffix}`),
    token_family_id: session.tokenFamilyId,
    device_credential_id: session.deviceCredentialId,
    expires_in: 1800,
    refresh_expires_in: 30 * 86400,
  }), { status: 200, headers: { "Content-Type": "application/json" } });
}

async function fixture() {
  const asyncItems = new Map();
  const secureItems = new Map();
  const requests = [];
  let storageGate = null;
  let cleanupGate = null;
  let failClear = false;
  let failWrite = false;
  const client = loadMobileClient({
    AbortController,
    fetch: (url, options) => {
      const response = deferred();
      requests.push({ url, refreshToken: JSON.parse(options.body).refresh_token, response });
      return response.promise;
    },
  });
  const auth = loadTsModule(mobilePath("src/store/auth.ts"), {
    require: (id) => {
      if (id === "../api/client") return client;
      if (id === "../api/client/nativeTlsTrust") {
        return {
          configureNativeTlsTrust: async () => undefined,
          clearNativeTlsTrust: async () => {
            if (failClear) throw new Error("test native cleanup failed");
          },
        };
      }
      if (id === "@react-native-async-storage/async-storage") return {
        __esModule: true,
        default: {
          getItem: async (key) => asyncItems.get(key) ?? null,
          setItem: async (key, value) => {
            if (storageGate && key === SESSION_KEY) {
              const gate = storageGate;
              storageGate = null;
              gate.entered.resolve();
              await gate.released.promise;
            }
            if (failWrite) throw new Error("test storage write failed");
            asyncItems.set(key, value);
          },
          removeItem: async (key) => { asyncItems.delete(key); },
        },
      };
      if (id === "expo-secure-store") return {
        getItemAsync: async (key) => secureItems.get(key) ?? null,
        setItemAsync: async (key, value) => { secureItems.set(key, value); },
        deleteItemAsync: async (key) => {
          if (cleanupGate) {
            cleanupGate.pendingDeletes += 1;
            if (cleanupGate.pendingDeletes === 2) cleanupGate.entered.resolve();
            await cleanupGate.released.promise;
          }
          secureItems.delete(key);
        },
      };
      return require(id);
    },
  });
  const manager = loadTsModule(mobilePath("src/sessionManager.ts"), {
    require: (id) => {
      if (id === "./api/client") return client;
      if (id === "./store/auth") return auth;
      return require(id);
    },
  });
  const original = originalSession();
  await manager.saveSession(original);
  return {
    original, manager, auth, client, requests, asyncItems, secureItems,
    blockPersistence() {
      const gate = { entered: deferred(), released: deferred() };
      storageGate = gate;
      return gate;
    },
    failPersistence() { failWrite = true; },
    allowPersistence() { failWrite = false; },
    blockFailedCleanup() {
      failClear = true;
      cleanupGate = { entered: deferred(), released: deferred(), pendingDeletes: 0 };
      return cleanupGate;
    },
  };
}

function layoutHarness(test) {
  let appStateListener;
  let timerId = 0;
  const timers = new Map();
  const state = { session: test.original, loadState: "ready", loadAttempt: 0, refreshAttempt: 0 };
  const context = {
    consentGate: "done", sessionLocked: false, session: test.original, sessionRefreshAttempt: 0,
    callbackLockEpoch: 0,
    appStateRef: { current: "active" }, sessionLockEpochRef: { current: 0 },
    AppState: {
      currentState: "active",
      addEventListener: (_event, listener) => {
        appStateListener = listener;
        return { remove: () => { appStateListener = undefined; } };
      },
    },
    ...test.manager,
    ...loadTsModule(mobilePath("src/sessionLifecycle.ts")),
    AuthExpiredError: test.client.AuthExpiredError,
    router: { replace: () => undefined }, resetShellState: () => undefined,
    reduceRemoteInputGrant: () => null, setRemoteInputGrant: () => undefined,
    setSession: (value) => { state.session = typeof value === "function" ? value(state.session) : value; },
    setSessionLocked: (value) => { context.sessionLocked = value; },
    setSessionLoadState: (value) => { state.loadState = value; },
    setSessionLoadAttempt: (value) => { state.loadAttempt = value(state.loadAttempt); },
    setSessionRefreshAttempt: (value) => {
      state.refreshAttempt = typeof value === "function" ? value(state.refreshAttempt) : value;
    },
    setTimeout: (callback) => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: (id) => { timers.delete(id); },
  };
  const evaluate = (source, invoke = true) => {
    const compiled = ts.transpileModule(`(${source})${invoke ? "()" : ""}`, {
      compilerOptions: { target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return vm.runInNewContext(compiled, context);
  };
  context.clearLocalSessionOrShowRecovery = evaluate(callbacks.get("clearLocalSessionOrShowRecovery"), false);
  evaluate(appStateEffect);
  return {
    state, context,
    startTimer: () => evaluate(timerEffect),
    startLoad: () => evaluate(loadEffect),
    clear: context.clearLocalSessionOrShowRecovery,
    paired: (session) => evaluate(callbacks.get("handlePaired"), false)(session),
    transition(next) { context.AppState.currentState = next; appStateListener(next); },
    fireTimer() {
      const [id, callback] = timers.entries().next().value ?? [];
      assert.ok(callback, "scheduled root refresh exists");
      timers.delete(id);
      callback();
    },
  };
}

async function foregroundSharesNetworkAndPersistence() {
  const test = await fixture();
  const layout = layoutHarness(test);
  const cleanupTimer = layout.startTimer();
  layout.fireTimer();
  await flush();
  assert.equal(test.requests.length, 1);
  layout.transition("background");
  cleanupTimer();
  layout.transition("active");
  const cleanupLoad = layout.startLoad();
  await flush();
  assert.equal(test.requests.length, 1, "foreground shares the request from the cleaned-up timer effect");
  const gate = test.blockPersistence();
  test.requests[0].response.resolve(refreshResponse(test.original));
  await gate.entered.promise;
  assert.equal(layout.state.loadState, "loading", "credentials stay hidden until persistence finishes");
  const cleanupDuringWrite = layout.startLoad();
  await flush();
  assert.equal(test.requests.length, 1, "unlock during a SecureStore/metadata write joins the same rotation");
  assert.equal(layout.state.loadState, "loading");
  gate.released.resolve();
  await flush();
  assert.equal(test.requests.length, 1, "a queued authenticated read must not rotate the just-written token again");
  assert.equal(layout.state.session.token, `${test.original.token}-next`);
  assert.equal(layout.state.loadState, "ready");
  assert.equal(test.secureItems.get(REFRESH_TOKEN_KEY), refreshToken(`${test.original.refreshToken}-next`));
  cleanupLoad();
  cleanupDuringWrite();
  const next = layout.state.session;
  const nextRotation = test.manager.refreshAndStoreSession(next);
  await flush();
  assert.equal(test.requests.length, 2, "a later rotation is permitted after durable persistence");
  assert.equal(test.requests[1].refreshToken, next.refreshToken);
  test.requests[1].response.resolve(refreshResponse(next, "later"));
  await nextRotation;
}

async function clearDuringNetwork() {
  const test = await fixture();
  const layout = layoutHarness(test);
  const cleanup = layout.startTimer();
  layout.fireTimer();
  await flush();
  layout.clear();
  await flush();
  test.requests[0].response.resolve(refreshResponse(test.original));
  await flush();
  assert.equal(layout.state.session, null, "a refresh completing after logout cannot revive the UI session");
  assert.equal(await test.auth.loadSession(), null, "a refresh completing after logout cannot restore stored credentials");
  assert.equal(layout.state.loadState, "ready");
  cleanup();
}

async function pairDuringNetwork(status) {
  const test = await fixture();
  const layout = layoutHarness(test);
  const cleanup = layout.startTimer();
  layout.fireTimer();
  await flush();
  const paired = originalSession("paired");
  await test.manager.saveSession(paired);
  layout.paired(paired);
  test.requests[0].response.resolve(status === 200 ? refreshResponse(test.original) :
    new Response(JSON.stringify({ detail: "Old session revoked" }), { status }));
  await flush();
  assert.equal(layout.state.session.token, paired.token, "the late old response cannot replace or clear new pairing UI");
  assert.equal((await test.auth.loadSession()).token, paired.token, "the new pairing survives a late success or 401");
  assert.equal(layout.state.loadState, "ready");
  cleanup();
}

async function mutationDuringPersistence(pairFresh) {
  const test = await fixture();
  const gate = test.blockPersistence();
  const rotation = test.manager.refreshAndStoreSession(test.original);
  await flush();
  test.requests[0].response.resolve(refreshResponse(test.original));
  await gate.entered.promise;
  const paired = originalSession("paired");
  const mutation = pairFresh ? test.manager.saveSession(paired) : test.manager.clearSession();
  gate.released.resolve();
  assert.equal(await rotation, null, "mutations invalidate refreshes before a pending write finishes");
  await mutation;
  const stored = await test.auth.loadSession();
  assert.equal(stored?.token ?? null, pairFresh ? paired.token : null, "the requested mutation wins the serialized storage queue");
}

async function staleSessionAndFailedPersistence() {
  const stale = await fixture();
  await stale.manager.saveSession(originalSession("paired"));
  assert.equal(await stale.manager.refreshAndStoreSession(stale.original), null);
  assert.equal(stale.requests.length, 0, "a stale scheduled session must not send its old refresh token");

  const failed = await fixture();
  const rotation = failed.manager.refreshAndStoreSession(failed.original);
  const rejected = assert.rejects(rotation, /test storage write failed/);
  await flush();
  failed.failPersistence();
  failed.requests[0].response.resolve(refreshResponse(failed.original));
  await rejected;
  assert.equal(failed.secureItems.get(TOKEN_KEY), undefined);
  assert.equal(failed.secureItems.get(REFRESH_TOKEN_KEY), undefined);
  assert.equal(await failed.manager.loadAndRefreshSession(), null, "failed persistence fails closed without reusing the consumed token");
  assert.equal(failed.requests.length, 1);
}

async function staleCallbackDoesNotDisplaceRotation() {
  const test = await fixture();
  const rotation = test.manager.refreshAndStoreSession(test.original);
  await flush();
  const stale = test.manager.refreshAndStoreSession(originalSession("obsolete"));
  const restored = test.manager.loadAndRefreshSession();
  await flush();
  assert.equal(test.requests.length, 1, "a stale family callback cannot displace the current family's shared request");
  test.requests[0].response.resolve(refreshResponse(test.original));
  assert.equal(await stale, null);
  assert.equal((await rotation).token, (await restored).token);
}

async function failedCleanupWaitsForDeletes(pairAfterFailedRefresh) {
  const test = await fixture();
  const gate = test.blockFailedCleanup();
  let completion;
  if (pairAfterFailedRefresh) {
    completion = test.manager.refreshAndStoreSession(test.original);
    await flush();
    test.failPersistence();
    test.requests[0].response.resolve(refreshResponse(test.original));
  } else {
    completion = assert.rejects(test.manager.clearSession(), /test native cleanup failed/);
  }
  await gate.entered.promise;
  test.allowPersistence();
  const paired = originalSession("paired");
  let saved = false;
  const save = test.manager.saveSession(paired).then(() => { saved = true; });
  await flush();
  assert.equal(saved, false, "new pairing storage must wait for every old deletion, even after another cleanup backend fails");
  gate.released.resolve();
  if (pairAfterFailedRefresh) {
    assert.equal(await completion, null, "failed old rotation cannot report a failure against the new pairing");
  } else {
    await completion;
  }
  await save;
  await flush();
  assert.equal((await test.auth.loadSession()).token, paired.token, "no late cleanup deletes the new pairing's credentials");
}

async function runSessionManagerSmoke() {
  await foregroundSharesNetworkAndPersistence();
  await clearDuringNetwork();
  await pairDuringNetwork(200);
  await pairDuringNetwork(401);
  await mutationDuringPersistence(false);
  await mutationDuringPersistence(true);
  await staleSessionAndFailedPersistence();
  await staleCallbackDoesNotDisplaceRotation();
  await failedCleanupWaitsForDeletes(false);
  await failedCleanupWaitsForDeletes(true);
  console.log("Mobile shared session manager race smoke passed (11 scenarios)");
}

module.exports = { runSessionManagerSmoke };
if (require.main === module) {
  runSessionManagerSmoke().catch((error) => { console.error(error); process.exitCode = 1; });
}
