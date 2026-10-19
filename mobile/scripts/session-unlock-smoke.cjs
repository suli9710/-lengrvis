const assert = require("node:assert/strict");
const { loadTsModule, mobilePath } = require("./behavior-smoke-helpers.cjs");

async function runSessionUnlockSmoke() {
  const removed = [];
  let tlsCleared = false;
  const metadata = { baseUrl: "https://mobile.example.test", deviceId: "d", tokenFamilyId: "f", deviceCredentialId: "c" };
  const storage = { getItem: async () => JSON.stringify(metadata), removeItem: async key => removed.push(key) };
  const api = {
    AuthExpiredError: class extends Error {}, InsecureLanBaseUrlError: class extends Error {},
    describeBaseUrlSecurity: url => ({ normalizedBaseUrl: url, isInsecureLan: false, isLoopback: false }),
    mergeBaseUrlSecurityMetadata: value => value, normalizePairingSecurityMetadata: () => undefined,
    clearRemoteInputGrantTokens: () => {}
  };
  const auth = loadTsModule(mobilePath("src/store/auth.ts"), { require: name => {
    if (name === "@react-native-async-storage/async-storage") return { __esModule: true, default: storage };
    if (name === "expo-secure-store") return {
      getItemAsync: async () => { throw new Error("User canceled authentication"); },
      deleteItemAsync: async key => removed.push(key)
    };
    if (name === "../api/client") return api;
    if (name === "../api/client/nativeTlsTrust") return {
      clearNativeTlsTrust: async () => { tlsCleared = true; }, configureNativeTlsTrust: async () => {}
    };
    throw new Error(`Unexpected import: ${name}`);
  } });
  await assert.rejects(auth.loadSession(), error => error.name === "SessionUnlockError");
  assert.deepEqual(removed, [], "authentication cancellation must preserve stored pairing credentials");
  assert.equal(tlsCleared, false, "authentication cancellation must preserve certificate trust");
}

module.exports = { runSessionUnlockSmoke };
