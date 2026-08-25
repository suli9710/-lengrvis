const assert = require("node:assert/strict");
const fs = require("node:fs");

const { mobilePath } = require("./behavior-smoke-helpers.cjs");

function assertSourceIncludes(source, expected, message) {
  assert.ok(source.includes(expected), `${message}: expected source to include ${JSON.stringify(expected)}`);
}

function assertSourceMatches(source, pattern, message) {
  assert.match(source, pattern, message);
}

function assertAppShellSourceAssertions() {
  const source = fs.readFileSync(mobilePath("app/_layout.tsx"), "utf8");
  assertSourceIncludes(source, 'type SessionLoadState = "loading" | "ready" | "failed";', "App must model stored-session loading explicitly");
  assertSourceIncludes(source, 'testID="app-session-load-screen"', "App loading/recovery screen must have a stable test id");
  assertSourceIncludes(source, "正在安全读取或清理这台手机保存的配对状态", "App must describe stored-session loading and cleanup as safe local recovery");
  assertSourceIncludes(source, "手机没有读到可用的本地会话", "App must explain failed stored-session recovery without raw storage errors");
  assert.doesNotMatch(source, /AsyncStorage|SecureStore|Error:/, "App session recovery UX must not expose storage internals or raw errors");
  assertSourceMatches(
    source,
    /const resetShellState = useCallback\(\(\) => \{[\s\S]*clearRemoteInputGrantTokens\(\);[\s\S]*setRemoteInputGrant\(\(current\) => reduceRemoteInputGrant\(current, \{ type: "cleared" \}\)\);[\s\S]*router\.replace\("\/home"\);[\s\S]*\}, \[router\]\);/,
    "App shell reset must clear remote input grants and return to the home tab",
  );
  assert.doesNotMatch(source, /setSelectedApproval|setActiveScreen/, "Expo Router shell must not keep legacy selected-approval or active-screen state");
  assertSourceMatches(
    source,
    /const clearLocalSessionOrShowRecovery = useCallback\(\(\) => \{[\s\S]*resetShellState\(\);[\s\S]*setSession\(null\);[\s\S]*setSessionLoadState\("loading"\);[\s\S]*void clearSession\(\)[\s\S]*\.then\(\(\) => \{[\s\S]*setSessionLoadState\("ready"\);[\s\S]*router\.replace\("\/"\);[\s\S]*\}\)[\s\S]*\.catch\(\(\) => \{[\s\S]*setSessionLoadState\("failed"\);[\s\S]*router\.replace\("\/"\);[\s\S]*\}\);[\s\S]*\}, \[resetShellState, router\]\);/,
    "Stored session cleanup must drop in-memory approval/session/grant state before async storage clearing and fail closed into recovery",
  );
  assertSourceIncludes(source, "const handlePaired = useCallback((nextSession: PairingSession) => {", "Pairing completion must reset shell state through one handler");
  assertSourceMatches(
    source,
    /const handlePaired = useCallback\(\(nextSession: PairingSession\) => \{[\s\S]*sessionLockEpochRef\.current !== callbackLockEpoch[\s\S]*resetShellState\(\);[\s\S]*setSessionLoadState\("ready"\);[\s\S]*setSession\(nextSession\);[\s\S]*router\.replace\("\/home"\);[\s\S]*\}, \[callbackLockEpoch, resetShellState, router\]\);/,
    "Pairing completion must not preserve old approval detail or remote input grants",
  );
  assertSourceIncludes(source, "onPairFresh={clearLocalSessionOrShowRecovery}", "Fresh pairing from recovery must clear stored credentials through the recovery-safe cleanup path");
  assert.doesNotMatch(
    source,
    /clearSession\(\)\.catch\(\(\) => undefined\)/,
    "App must not claim local session cleanup succeeded when storage clearing fails",
  );
  assertSourceMatches(
    source,
    /<MobileCompanionProvider[\s\S]*onSelectApproval=\{\(approval\) => router\.push\(\{ pathname: "\/approval\/\[id\]", params: \{ id: approval\.id \} \}\)\}[\s\S]*onSessionExpired=\{clearLocalSessionOrShowRecovery\}[\s\S]*session=\{session\}/,
    "Companion routes must send approval navigation and auth expiry through the root recovery shell",
  );
  const approvalsSource = fs.readFileSync(mobilePath("src/screens/ApprovalsScreen.tsx"), "utf8");
  assert.doesNotMatch(
    approvalsSource,
    /from "\.\.\/store\/auth"|clearSession\(\)|finally\(onUnpair\)/,
    "ApprovalsScreen must not bypass App session recovery when clearing local credentials",
  );
  assertSourceIncludes(source, "onSessionExpired={clearLocalSessionOrShowRecovery}", "Remote and companion screens must be able to clear an expired mobile session");
  assertSourceIncludes(source, 'accessibilityRole={isLoading ? "progressbar" : "alert"}', "Stored-session recovery must expose loading and failed states to Android accessibility");
  assertSourceIncludes(source, 'accessibilityHint="清理本地会话并回到配对页面"', "Fresh pairing recovery action must explain that it clears local session state");
  assertSourceIncludes(source, "resetShellState();", "Unpair must clear selected approval and remote input grant shell state through the shared recovery path");
  assertSourceMatches(
    source,
    /let isActive = true;[\s\S]*getApprovalDetail\(session, approvalId\)[\s\S]*if \(!isActive\) return;[\s\S]*router\.push\(\{ pathname: "\/approval\/\[id\]", params: \{ id: detail\.approval\.id \} \}\);[\s\S]*return \(\) => \{[\s\S]*isActive = false;[\s\S]*subscription\.remove\(\);[\s\S]*\};/,
    "Notification-opened approval detail loads must not rehydrate stale selections after unpair and must leave remote screen to show the detail",
  );
}

module.exports = { assertAppShellSourceAssertions };
