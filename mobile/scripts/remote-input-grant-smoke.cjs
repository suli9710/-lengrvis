const assert = require("node:assert/strict");
const fs = require("node:fs");

const {
  acceptWebSocketUpgrade,
  assertAcceptedWebSocket,
  assertInsecureLanError,
  assertJsonRequest,
  assertWebSocketTokenTransport,
  connectWebSocket,
  jsonResponse,
  loadMobileClient,
  loadTsModule,
  mobilePath,
  rejectWebSocketUpgrade,
  startHttpWsSmokeServer,
} = require("./behavior-smoke-helpers.cjs");

const WS_PROTOCOL_PREFIX = "lengrvis.mobile.token.";
const SESSION_TOKEN = "session-token";
const DEVICE_ID = "device-1";
const ACTIVE_GRANT_ID = "grant/slash id";
const APPROVAL_GRANT_ID = "grant-approval";
const EXPIRING_GRANT_ID = "grant-expiring";
const INVALID_TOKEN_GRANT_ID = "grant-invalid-token";
const START_TIME = Date.now();
const EXPIRY_TIME = START_TIME + 5 * 60 * 1000;

function bindingRefForGrantId(grantId) {
  return `[remote-input-binding:${grantId.replace(/[^a-z0-9_-]/gi, "_")}]`;
}

function makeGrant(id, overrides = {}) {
  return {
    id,
    status: "active",
    scope: "remote:input",
    created_at: new Date(START_TIME).toISOString(),
    expires_at: new Date(EXPIRY_TIME).toISOString(),
    binding_ref: bindingRefForGrantId(id),
    ...overrides,
  };
}

function makeSession(client, baseUrl, token = SESSION_TOKEN) {
  const security = client.describeBaseUrlSecurity(baseUrl);
  return {
    baseUrl: security.normalizedBaseUrl,
    baseUrlSecurity: security,
    deviceId: DEVICE_ID,
    token,
  };
}

function tokenForGrantId(grantId) {
  if (grantId === ACTIVE_GRANT_ID) return "grant-token-active";
  if (grantId === EXPIRING_GRANT_ID) return "grant-token-expiring";
  if (grantId === INVALID_TOKEN_GRANT_ID) return "grant token invalid";
  return `grant-token-${grantId.replace(/[^a-z0-9_-]/gi, "_")}`;
}

function decodeGrantTokenPath(pathname, suffix = "") {
  const prefix = "/api/mobile/remote-input-grants/";
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) return null;
  return decodeURIComponent(pathname.slice(prefix.length, pathname.length - suffix.length));
}

function decodeApprovalPath(pathname, suffix = "") {
  const prefix = "/api/mobile/approvals/";
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) return null;
  return decodeURIComponent(pathname.slice(prefix.length, pathname.length - suffix.length));
}

function assertSourceIncludes(source, expected, message) {
  assert.ok(source.includes(expected), `${message}: expected source to include ${JSON.stringify(expected)}`);
}

function assertAuthExpiredError(error) {
  assert.equal(error?.name, "AuthExpiredError");
  assert.equal(error?.code, "auth_expired");
  return true;
}

function functionSource(source, name) {
  const start = source.indexOf(`function ${name}`);
  assert.notEqual(start, -1, `RemoteScreen must define ${name}`);
  const nextFunction = source.indexOf("\nfunction ", start + 1);
  const styles = source.indexOf("\nconst styles", start + 1);
  const end = [nextFunction, styles].filter((index) => index > start).sort((left, right) => left - right)[0] ?? source.length;
  return source.slice(start, end);
}

function assertRemoteScreenBeginnerCopy() {
  const source = fs.readFileSync(mobilePath("src/screens/RemoteScreen.tsx"), "utf8");
  const appConfig = JSON.parse(fs.readFileSync(mobilePath("app.json"), "utf8"));
  const beginnerCopy = [
    "只读观看",
    "已授权输入",
    "授权剩余",
    "结束接管",
    "只读屏幕查看",
    "点击、文字和按键仍需电脑端审批",
    "远程输入仍需电脑端审批",
    "发送文字",
    "回车",
    "上翻",
    "下翻",
    "安全连接已开启",
    "需要确认这台电脑",
    "连接已阻止",
    "当前网络连接不够安全",
    "远程输入连接失败",
    "输入授权已过期",
    "电脑端已结束或拒绝这次远程输入",
    "这台手机没有远程输入权限",
  ];
  for (const copy of beginnerCopy) {
    assertSourceIncludes(source, copy, `RemoteScreen beginner copy must explain ${copy}`);
  }

  const transportCopy = functionSource(source, "remoteTransportNotice");
  assert.doesNotMatch(
    transportCopy,
    /token|WebSocket protocol|URL|SHA-256|fingerprint|formatTlsFingerprint|security\.host|HTTPS \/|LAN HTTP|HTTP\/ws/,
    "Remote transport notice must not expose protocol, host, token, or certificate internals to beginners",
  );
  assert.doesNotMatch(
    source,
    /if \(error instanceof Error && error\.message\) return error\.message;|payload\.message \|\|/,
    "Remote input failure UI must not surface raw backend or token errors",
  );
  assertSourceIncludes(source, "onSessionExpired: () => void;", "RemoteScreen must accept an app-level session-expired callback");
  assertSourceIncludes(source, "if (currentError instanceof AuthExpiredError)", "RemoteScreen must detect locally expired mobile sessions");
  assertSourceIncludes(source, "if (transportBlocked)", "RemoteScreen must avoid opening remote sockets when transport is blocked");
  assertSourceIncludes(
    source,
    'const canRetry = !transportBlocked && (connection === "offline" || !!error);',
    "RemoteScreen must not offer reconnect loops while safe transport requirements block remote access",
  );
  assertSourceIncludes(
    source,
    "安全连接开启前，手机不会显示屏幕或发送远程输入。",
    "RemoteScreen must explain blocked transport as a product state, not a transient connection failure",
  );
  assertSourceIncludes(
    source,
    "const grantExpiryDelayMs = effectiveGrant ? remoteInputGrantExpiryDelayMs(effectiveGrant, nowMs) : null;",
    "RemoteScreen must derive grant expiry from the shared remote input grant helper",
  );
  assertSourceIncludes(
    source,
    'setInputError(grantExpired ? "输入授权已过期。请在电脑端重新授权。" : "");',
    "RemoteScreen must disable input and explain when the active remote input grant expires",
  );
  assertSourceIncludes(source, "clearRemoteFrame();", "RemoteScreen must clear stale frames when security or app lifecycle requires it");
  assertSourceIncludes(source, "webSocketCloseLooksSessionExpired", "RemoteScreen must route expired mobile sessions through the app-level expiry callback");
  assertSourceIncludes(source, "webSocketCloseLooksRemoteInputGrantEnded", "RemoteScreen must treat ended remote input grants as a disabled input state");
  assertSourceIncludes(source, "onRemoteInputGrantRevoked({ ...effectiveGrant", "RemoteScreen must clear active grants after terminal remote-input socket closes");
  assertSourceIncludes(source, "const terminalGrantFailure = remoteInputGrantFailureIsTerminal(currentError);", "RemoteScreen must detect terminal grant failures before the remote-input WebSocket opens");
  assertSourceIncludes(source, "setLocallyRevokedGrantId(effectiveGrant.id);", "RemoteScreen must immediately hide stale input grants after pre-WebSocket terminal failures");
  assert.match(
    source,
    /if \(state === "active"\) \{\s*if \(!pausedByUserRef\.current && !transportBlocked\) \{\s*setConnection\("connecting"\);\s*connect\(\);\s*if \(grantUsable\) void connectInput\(\);/,
    "RemoteScreen foreground reconnect must restore screen viewing and only reconnect input for a currently usable grant",
  );
  assertSourceIncludes(
    source,
    'const remoteClickDisabled = !grantUsable || !frame || transportBlocked || connection !== "online";',
    "RemoteScreen must not send Android taps against paused, offline, or blocked frames",
  );
  assertSourceIncludes(source, "android_disableSound", "RemoteScreen remote viewer press target must use Android-specific touch behavior");
  assertSourceIncludes(source, "accessibilityHint={viewerAccessibilityHint}", "RemoteScreen viewer must explain when remote taps are available");
  assertSourceIncludes(source, 'accessibilityLiveRegion="polite"', "RemoteScreen connection and input status changes must be announced accessibly");
  assertSourceIncludes(source, "REMOTE_VIEWER_ZOOM_OPTIONS", "RemoteScreen must offer explicit zoom states for remote desktop viewing");
  assertSourceIncludes(source, "zoomRemoteViewerSurface", "RemoteScreen zoom controls must change the rendered viewer surface");
  assertSourceIncludes(source, "ScrollView", "RemoteScreen zoomed viewer must be pannable instead of cropping the desktop");
  assertSourceIncludes(source, 'sendRemoteInputEvent({ type: "type", text: textDraft }', "RemoteScreen must send remote text input events");
  assertSourceIncludes(source, 'sendRemoteInputEvent({ type: "key", key }', "RemoteScreen must send remote key press events");
  assertSourceIncludes(
    source,
    'const remoteInputControlsDisabled = remoteClickDisabled || inputConnection !== "online";',
    "RemoteScreen text and key controls must stay disabled until screen and input sockets are online",
  );
  assert.equal(appConfig.expo.orientation, "default", "Mobile app must support landscape for remote desktop viewing");
}

function assertApprovalDetailBeginnerSafety() {
  const approvalSafety = loadTsModule(mobilePath("src/approvalSafetyDisplay.ts"));
  const blocked = approvalSafety.approvalDecisionGuard({
    approval_type: "system_change",
    risk_level: "R4_FORBIDDEN_OR_HANDOFF",
    tool_effects: ["execute"],
    resource_kinds: ["system"],
  });
  assert.equal(blocked.approveBlockedReason, "这类请求不能在手机上批准；请回电脑端处理或直接拒绝。");
  assert.equal(blocked.tone, "danger");
  assert.match(blocked.title, /手机端不会批准/);
  assert.match(blocked.nextStep, /拒绝|电脑端/);
  assert.equal(
    approvalSafety.approvalApproveBlockedReason({
      engineering_boundary: { tool: { risk_level: "R4_FORBIDDEN_OR_HANDOFF" } },
    }),
    "这类请求不能在手机上批准；请回电脑端处理或直接拒绝。",
  );

  const destructiveWithPreview = approvalSafety.approvalDecisionGuard({
    approval_type: "file_operation",
    risk_level: "R3_DESTRUCTIVE_OR_SYSTEM",
    tool_effects: ["delete"],
    resource_kinds: ["file"],
    dry_run_summary: "将把 1 个文件移入回收站。",
  });
  assert.equal(destructiveWithPreview.approveBlockedReason, undefined);
  assert.equal(destructiveWithPreview.tone, "warning");
  assert.match(destructiveWithPreview.title, /批准前先核对范围/);
  assert.match(destructiveWithPreview.detail, /批准后电脑端才会继续执行/);
  assert.match(destructiveWithPreview.nextStep, /不确定就拒绝/);

  const destructiveWithoutPreview = approvalSafety.approvalDecisionGuard({
    approval_type: "cleanup_execute",
    risk_level: "R3_DESTRUCTIVE_OR_SYSTEM",
    tool_effects: ["delete"],
  });
  assert.equal(destructiveWithoutPreview.tone, "danger");
  assert.match(destructiveWithoutPreview.detail, /默认更安全的下一步是拒绝/);

  const dangerousPermission = approvalSafety.approvalDecisionGuard({
    approval_type: "tool_call",
    permission_mode: "danger-full-access",
    tool_effects: ["execute"],
    resource_kinds: ["system"],
    dry_run_summary: "将以更高权限执行命令。",
  });
  assert.equal(dangerousPermission.tone, "danger");
  assert.match(dangerousPermission.approveBlockedReason, /扩大电脑端执行权限/);
  assert.match(dangerousPermission.title, /不会扩大电脑权限/);
  assert.equal(
    approvalSafety.approvalListSafety({
      policy_mode: "unrestricted",
      tool_effects: ["execute"],
      resource_kinds: ["system"],
      dry_run_summary: "将执行管理员级命令。",
    }).approveBlockedReason,
    "此审批会扩大电脑端执行权限，不能在手机上批准；请回电脑端核对后手动处理或拒绝。",
  );

  const remoteInputAllowedDeviceMismatch = {
    approval_type: "remote_input",
    source: "remote_input",
    source_device_id: DEVICE_ID,
    source_grant_id: ACTIVE_GRANT_ID,
    allowed_device_ids: ["other-device"],
    required_mobile_scopes: ["remote:input"],
    diff_preview: { diff_preview: [{ action: "click" }] },
  };
  assert.match(
    approvalSafety.approvalApproveBlockedReason(remoteInputAllowedDeviceMismatch, {
      deviceId: DEVICE_ID,
      grantId: ACTIVE_GRANT_ID,
    }),
    /匹配当前手机的远控授权/,
    "remote-input approvals excluded by allowed_device_ids must be treated as device mismatches",
  );
  assert.match(
    approvalSafety.remoteInputMobileDecisionBlockedReason(remoteInputAllowedDeviceMismatch, {
      deviceId: DEVICE_ID,
      grantId: ACTIVE_GRANT_ID,
    }),
    /匹配当前手机的远控授权/,
    "remote-input device mismatches must block approval on mobile",
  );
  assert.equal(
    approvalSafety.remoteInputMobileDecisionBlockedReason(dangerousPermission, null),
    null,
    "ordinary non-remote approval blocks must still allow mobile rejection",
  );

  const source = fs.readFileSync(mobilePath("src/screens/ApprovalDetail.tsx"), "utf8");
  assertSourceIncludes(source, "批准前核对", "Approval detail must show a beginner decision guard");
  assertSourceIncludes(source, "不可批准", "Approval detail must label blocked approval as unavailable");
  assertSourceIncludes(source, "正在加载审批详情", "Approval detail must expose a loading state");
  assertSourceIncludes(source, "重新加载详情", "Approval detail must offer retry after detail load failures");
  assertSourceIncludes(source, "approvalApproveBlockedReason", "Approval detail must use approval block helper");
  assertSourceIncludes(source, "const activeGrantContext = useMemo", "Approval detail must bind approval safety to the active remote input grant");
  assertSourceIncludes(source, "latestApproveBlockedReason", "Approval detail must re-check blocked approval before submit");
  assertSourceIncludes(source, "latestMobileDecisionBlockedReason", "Approval detail must re-check remote-input grant mismatch before approving");
  assertSourceIncludes(source, "remoteInputMobileDecisionBlockedReason", "Approval detail must distinguish remote-input grant mismatch from normal rejectable blocks");
  assertSourceIncludes(source, "canShowDecisionRow", "Approval detail must not show decision buttons before details and safety guard load");
  assertSourceIncludes(source, "isRemoteInputGrantUsable(remoteInputGrant)", "Approval detail must not treat expired or revoked grants as active mobile decision context");
  assertSourceIncludes(source, "approval: latest.approval", "Approval detail must submit the latest approval details for client-side grant matching");
  assertSourceIncludes(source, "remoteInputGrant: usableRemoteInputGrant", "Approval detail must pass only a usable remote input grant into approval submission");
  assertSourceIncludes(source, "手机端不可批准", "Approval detail must tell the user when submit-time approval is blocked");
  assert.doesNotMatch(
    source,
    /Trust tier|Effects|Dry-run|risk:|trust:|deferred search/,
    "Approval detail must not expose internal English trust/risk labels in the phone UI",
  );
  assertSourceIncludes(source, "paddingBottom: Platform.select({ android: 32", "Approval detail bottom actions must leave Android navigation-bar space");
  assertSourceIncludes(source, "paddingBottom: Platform.select({ android: 152", "Approval detail scroll content must not hide behind fixed bottom actions");
  assertSourceIncludes(source, "minWidth: 0,", "Approval detail bottom action buttons must shrink cleanly on narrow phones");
  assertSourceIncludes(source, "textAlign: \"center\",", "Approval detail bottom action text must remain readable when Chinese copy wraps");
  assert.match(
    source,
    /disabled=\{isBusy \|\| Boolean\(mobileDecisionBlockedReason \|\| approveBlockedReason\)\}/,
    "Approval detail approve button must be disabled when the helper blocks mobile approval",
  );
  assert.match(
    source,
    /disabled=\{isBusy\}/,
    "Approval detail deny button must remain available as a safe stop action after details load",
  );
  assert.match(
    source,
    /accessibilityHint=\{mobileDecisionBlockedReason \|\| approveBlockedReason \|\| decisionGuard\.nextStep\}/,
    "Approval detail approve button must expose the safer next step as accessibility hint",
  );
}

function assertApprovalListBeginnerSafety() {
  const approvalSafety = loadTsModule(mobilePath("src/approvalSafetyDisplay.ts"));
  const highRiskNoBoundary = approvalSafety.approvalListSafety({
    approval_type: "cleanup_execute",
    risk_level: "R3_DESTRUCTIVE_OR_SYSTEM",
    tool_effects: ["delete"],
    message: 'args={"path":"C:\\Users\\Suli\\Desktop\\secret.txt","mobile_token":"abc"}',
  });
  assert.equal(highRiskNoBoundary.label, "缺少安全边界");
  assert.equal(highRiskNoBoundary.tone, "danger");
  assert.equal(
    highRiskNoBoundary.detail,
    "手机端不可批准；回电脑端核对试运行和影响范围。",
    "Approval list must not imply approval is available when a high-risk request lacks dry-run or scope",
  );
  assert.doesNotMatch(highRiskNoBoundary.detail, /args|path|token|C:\\|secret/i);

  const remoteInputMissingBinding = approvalSafety.approvalListSafety({
    approval_type: "remote_input",
    source: "remote_input",
    source_device_id: DEVICE_ID,
  });
  assert.equal(remoteInputMissingBinding.label, "远控授权不完整");
  assert.equal(remoteInputMissingBinding.tone, "danger");
  assert.match(remoteInputMissingBinding.detail, /不可批准|拒绝/);
  assert.doesNotMatch(remoteInputMissingBinding.detail, /source_|grant|token|device/i);

  const remoteInputApproval = {
    approval_type: "remote_input",
    source: "remote_input",
    source_device_id: DEVICE_ID,
    source_grant_id: ACTIVE_GRANT_ID,
    allowed_device_ids: [DEVICE_ID],
    required_mobile_scopes: ["remote:input"],
    diff_preview: { diff_preview: [{ action: "click" }] },
  };
  const remoteInputNoActiveGrant = approvalSafety.approvalListSafety(remoteInputApproval);
  assert.equal(remoteInputNoActiveGrant.label, "远控授权不匹配");
  assert.equal(remoteInputNoActiveGrant.tone, "danger");
  assert.match(remoteInputNoActiveGrant.approveBlockedReason, /匹配当前手机的远控授权/);
  assert.doesNotMatch(remoteInputNoActiveGrant.detail, /source_|grant|token|device/i);

  const remoteInputWrongActiveGrant = approvalSafety.approvalListSafety(remoteInputApproval, {
    deviceId: DEVICE_ID,
    grantId: "other-grant",
  });
  assert.equal(remoteInputWrongActiveGrant.label, "远控授权不匹配");
  assert.equal(remoteInputWrongActiveGrant.tone, "danger");

  const remoteInputDisallowedDevice = approvalSafety.approvalListSafety({
    ...remoteInputApproval,
    allowed_device_ids: ["other-device"],
  }, {
    deviceId: DEVICE_ID,
    grantId: ACTIVE_GRANT_ID,
  });
  assert.equal(remoteInputDisallowedDevice.label, "远控授权不匹配");
  assert.equal(remoteInputDisallowedDevice.tone, "danger");

  const remoteInputMatchingActiveGrant = approvalSafety.approvalListSafety(remoteInputApproval, {
    deviceId: DEVICE_ID,
    grantId: ACTIVE_GRANT_ID,
  });
  assert.equal(remoteInputMatchingActiveGrant.label, "批准前核对");
  assert.equal(remoteInputMatchingActiveGrant.tone, "warning");
  assert.equal(remoteInputMatchingActiveGrant.approveBlockedReason, undefined);

  const publicBindingApproval = {
    approval_type: "remote_input",
    source: "remote_input",
    required_mobile_scopes: ["remote:input"],
    remote_input_binding: {
      device_bound: true,
      grant_bound: true,
      requires_remote_input_scope: true,
      binding_ref: "[remote-input-binding:test]",
    },
    diff_preview: { diff_preview: [{ action: "click" }] },
  };
  assert.equal(
    approvalSafety.approvalListSafety(publicBindingApproval, {
      deviceId: DEVICE_ID,
      grantId: ACTIVE_GRANT_ID,
      bindingRef: "[remote-input-binding:test]",
    }).label,
    "批准前核对",
    "Mobile approval safety must accept backend public binding refs without raw device/grant ids",
  );
  assert.equal(
    approvalSafety.approvalListSafety(publicBindingApproval, {
      deviceId: DEVICE_ID,
      grantId: ACTIVE_GRANT_ID,
      bindingRef: "[remote-input-binding:other]",
    }).label,
    "远控授权不匹配",
    "Mobile approval safety must fail closed when the public binding ref does not match the active grant",
  );

  const forbidden = approvalSafety.approvalListSafety({
    engineering_boundary: { tool: { risk_level: "R4_FORBIDDEN_OR_HANDOFF" } },
  });
  assert.equal(forbidden.label, "手机不可批准");
  assert.equal(forbidden.tone, "danger");
  assert.match(forbidden.detail, /电脑端|拒绝/);

  const highRiskWithBoundary = approvalSafety.approvalListSafety({
    approval_type: "file_operation",
    risk_level: "R3_DESTRUCTIVE_OR_SYSTEM",
    tool_effects: ["delete"],
    resource_kinds: ["file"],
    dry_run_summary: "将把 1 个文件移入回收站。",
  });
  assert.equal(highRiskWithBoundary.label, "批准前核对");
  assert.equal(highRiskWithBoundary.tone, "warning");

  const safeDisplay = loadTsModule(mobilePath("src/safeDisplay.ts"));
  const redacted = safeDisplay.safeDisplayText(
    'Run tool args={"path":"C:\\Users\\Suli\\Desktop\\secret.txt","authorization":"Bearer abc.def"}',
  );
  assert.match(redacted, /已隐藏/);
  assert.doesNotMatch(redacted, /args|path|C:\\Users|secret\.txt|abc\.def|Bearer abc|authorization/i);

  const listSource = fs.readFileSync(mobilePath("src/screens/ApprovalsScreen.tsx"), "utf8");
  assertSourceIncludes(listSource, "const safety = approvalListSafety(", "Approval list cards must derive beginner-safe list copy");
  assertSourceIncludes(listSource, "isRemoteInputGrantUsable(remoteInputGrant)", "Approval list must ignore expired or revoked remote-input grants");
  assertSourceIncludes(listSource, "payload.remote_input_grants", "Approval stream connected snapshot must restore active remote-input grants after missed events");
  assertSourceIncludes(listSource, "onRemoteInputGrant(snapshotGrant)", "Approval stream connected snapshot must hand restored active grants to the app shell");
  assertSourceIncludes(listSource, "bindingRef: activeRemoteInputGrant.binding_ref", "Approval list safety copy must include the current usable remote-input public binding ref");
  assertSourceIncludes(listSource, "safety.label", "Approval cards must expose the safety label instead of only a generic pending badge");
  assertSourceIncludes(listSource, "safety.detail", "Pending approval cards must explain the safe next step without raw approval internals");
  assertSourceIncludes(listSource, "maxWidth: \"48%\",", "Approval card status badges must not crowd long Chinese titles on narrow phones");
}

function assertAppClearsRemoteInputGrantTokens() {
  const source = fs.readFileSync(mobilePath("App.tsx"), "utf8");
  assertSourceIncludes(source, "clearRemoteInputGrantTokens", "App shell must import the remote-input grant token cache reset helper");
  assertSourceIncludes(source, "const handleRemoteInputGrantRevoked", "App shell must handle desktop/device remote-input grant revoke events");
  assertSourceIncludes(source, "clearRemoteInputGrantTokens();", "App shell must clear cached remote-input bearer tokens when grant/session state is cleared");
}

async function main() {
  const client = loadMobileClient();
  const {
    isRemoteInputGrantUsable,
    mapViewerPointToRemote,
    reduceRemoteInputGrant,
    remoteInputGrantExpiryDelayMs,
    remoteInputGrantRemainingText,
  } = loadTsModule(mobilePath("src/remoteInputGrant.ts"));
  assertRemoteScreenBeginnerCopy();
  assertApprovalDetailBeginnerSafety();
  assertApprovalListBeginnerSafety();
  assertAppClearsRemoteInputGrantTokens();

  const state = {
    now: START_TIME,
    grants: new Map([
      [ACTIVE_GRANT_ID, makeGrant(ACTIVE_GRANT_ID)],
      [APPROVAL_GRANT_ID, makeGrant(APPROVAL_GRANT_ID)],
      [EXPIRING_GRANT_ID, makeGrant(EXPIRING_GRANT_ID)],
      [INVALID_TOKEN_GRANT_ID, makeGrant(INVALID_TOKEN_GRANT_ID, { expires_at: new Date(EXPIRY_TIME + 60000).toISOString() })],
    ]),
    approvals: new Map([
      [
        "approval-active",
        {
          id: "approval-active",
          task_id: "task-remote-input",
          approval_type: "remote_input",
          message: "Approve remote input click",
          diff_preview: {},
          status: "pending",
          created_at: new Date(START_TIME).toISOString(),
          required_mobile_scopes: ["remote:input"],
          remote_input_binding: {
            device_bound: true,
            grant_bound: true,
            requires_remote_input_scope: true,
            binding_ref: bindingRefForGrantId(ACTIVE_GRANT_ID),
            matches_current_device: true,
          },
        },
      ],
      [
        "approval-explicit-grant",
        {
          id: "approval-explicit-grant",
          task_id: "task-remote-input-explicit",
          approval_type: "remote_input",
          message: "Approve explicit remote input click",
          diff_preview: {},
          status: "pending",
          created_at: new Date(START_TIME).toISOString(),
          source_device_id: DEVICE_ID,
          source_grant_id: APPROVAL_GRANT_ID,
          allowed_device_ids: [DEVICE_ID],
          required_mobile_scopes: ["remote:input"],
        },
      ],
      [
        "approval-ordinary",
        {
          id: "approval-ordinary",
          task_id: "task-ordinary",
          approval_type: "tool_call",
          message: "Approve a normal mobile action",
          diff_preview: { diff_preview: [{ action: "read" }] },
          status: "pending",
          created_at: new Date(START_TIME).toISOString(),
        },
      ],
    ]),
    tokenToGrantId: new Map(),
  };

  const server = await startHttpWsSmokeServer({
    handleRequest: ({ res, url, request }) => {
      const grantTokenId = decodeGrantTokenPath(url.pathname, "/token");
      if (request.method === "POST" && grantTokenId) {
        assertJsonRequest(request, {
          method: "POST",
          path: `/api/mobile/remote-input-grants/${encodeURIComponent(grantTokenId)}/token`,
          authorization: `Bearer ${SESSION_TOKEN}`,
        });
        const grant = state.grants.get(grantTokenId);
        if (!grant) {
          jsonResponse(res, 404, { detail: "Grant not found" });
          return true;
        }
        if (grant.revoked_at || grant.status === "revoked") {
          jsonResponse(res, 403, { detail: "Grant revoked" });
          return true;
        }
        if (Date.parse(grant.expires_at) <= state.now) {
          jsonResponse(res, 410, { detail: "Grant expired" });
          return true;
        }
        const token = tokenForGrantId(grantTokenId);
        state.tokenToGrantId.set(token, grantTokenId);
        jsonResponse(res, 200, {
          token,
          token_type: "Bearer",
          grant_id: grantTokenId,
          device_id: DEVICE_ID,
          expires_at: grant.expires_at,
          expires_in: Math.ceil((Date.parse(grant.expires_at) - state.now) / 1000),
          grant,
        });
        return true;
      }

      const revokeGrantId = decodeGrantTokenPath(url.pathname);
      if (request.method === "DELETE" && revokeGrantId) {
        assertJsonRequest(request, {
          method: "DELETE",
          path: `/api/mobile/remote-input-grants/${encodeURIComponent(revokeGrantId)}`,
          authorization: `Bearer ${SESSION_TOKEN}`,
        });
        const grant = state.grants.get(revokeGrantId);
        if (!grant) {
          jsonResponse(res, 404, { detail: "Grant not found" });
          return true;
        }
        const revoked = {
          ...grant,
          status: "revoked",
          revoked_at: new Date(START_TIME + 2 * 60 * 1000).toISOString(),
        };
        state.grants.set(revokeGrantId, revoked);
        jsonResponse(res, 200, revoked);
        return true;
      }

      const approvalDetailId = decodeApprovalPath(url.pathname);
      if (request.method === "GET" && approvalDetailId) {
        assertJsonRequest(request, {
          method: "GET",
          path: `/api/mobile/approvals/${encodeURIComponent(approvalDetailId)}`,
          authorization: `Bearer ${SESSION_TOKEN}`,
        });
        const approval = state.approvals.get(approvalDetailId);
        if (!approval) {
          jsonResponse(res, 404, { detail: "Approval not found" });
          return true;
        }
        jsonResponse(res, 200, {
          approval,
          task: null,
          plan: null,
          preview: approval.diff_preview,
        });
        return true;
      }

      const approvalDecisionId = decodeApprovalPath(url.pathname, "/decision");
      if (request.method === "POST" && approvalDecisionId) {
        const approval = state.approvals.get(approvalDecisionId);
        if (!approval) {
          jsonResponse(res, 404, { detail: "Approval not found" });
          return true;
        }
        const bindingRef = approval.remote_input_binding?.binding_ref;
        const grantId = approval.source_grant_id || [...state.grants.entries()].find(([, grant]) => grant.binding_ref === bindingRef)?.[0] || "";
        assertJsonRequest(request, {
          method: "POST",
          path: `/api/mobile/approvals/${encodeURIComponent(approvalDecisionId)}/decision`,
          authorization: `Bearer ${tokenForGrantId(grantId)}`,
          body: { decision: "approved" },
        });
        if (state.tokenToGrantId.get(tokenForGrantId(grantId)) !== grantId) {
          jsonResponse(res, 403, { detail: "Unknown grant token" });
          return true;
        }
        const decided = {
          ...approval,
          status: "approved",
          decided_at: new Date(state.now).toISOString(),
        };
        state.approvals.set(approvalDecisionId, decided);
        jsonResponse(res, 200, decided);
        return true;
      }

      const approvalApproveId = decodeApprovalPath(url.pathname, "/approve");
      const approvalRejectId = decodeApprovalPath(url.pathname, "/reject");
      const genericDecisionId = approvalApproveId || approvalRejectId;
      if (request.method === "POST" && genericDecisionId) {
        assertJsonRequest(request, {
          method: "POST",
          path: `/api/mobile/approvals/${encodeURIComponent(genericDecisionId)}/${approvalApproveId ? "approve" : "reject"}`,
          authorization: `Bearer ${SESSION_TOKEN}`,
        });
        const approval = state.approvals.get(genericDecisionId);
        if (!approval) {
          jsonResponse(res, 404, { detail: "Approval not found" });
          return true;
        }
        const decided = {
          ...approval,
          status: approvalApproveId ? "approved" : "rejected",
          decided_at: new Date(state.now).toISOString(),
        };
        state.approvals.set(genericDecisionId, decided);
        jsonResponse(res, 200, decided);
        return true;
      }

      return false;
    },
    handleUpgrade: ({ req, socket, url, upgrade }) => {
      if (url.pathname !== "/ws/remote/input") {
        rejectWebSocketUpgrade(socket, 404, "Unknown WebSocket path");
        return;
      }
      assert.equal(url.search, "", "remote input WebSocket upgrade must not carry query auth");
      const tokenProtocol = upgrade.protocols.find((protocol) => protocol.startsWith(WS_PROTOCOL_PREFIX));
      const token = tokenProtocol?.slice(WS_PROTOCOL_PREFIX.length);
      const grantId = token ? state.tokenToGrantId.get(token) : undefined;
      const grant = grantId ? state.grants.get(grantId) : undefined;
      if (!grant || !tokenProtocol) {
        rejectWebSocketUpgrade(socket, 401, "Missing or unknown grant token");
        return;
      }
      if (grant.revoked_at || grant.status === "revoked") {
        rejectWebSocketUpgrade(socket, 403, "Grant revoked");
        return;
      }
      if (Date.parse(grant.expires_at) <= state.now) {
        rejectWebSocketUpgrade(socket, 410, "Grant expired");
        return;
      }
      upgrade.accepted = true;
      acceptWebSocketUpgrade(req, socket, tokenProtocol);
    },
  });

  try {
    const activeGrant = state.grants.get(ACTIVE_GRANT_ID);
    assert.equal(isRemoteInputGrantUsable(activeGrant, START_TIME), true);
    assert.equal(isRemoteInputGrantUsable(activeGrant, EXPIRY_TIME - 1), true);
    assert.equal(isRemoteInputGrantUsable(activeGrant, EXPIRY_TIME), false);
    assert.equal(remoteInputGrantExpiryDelayMs(activeGrant, START_TIME), 300000);
    assert.equal(remoteInputGrantExpiryDelayMs({ ...activeGrant, expires_at: "" }, START_TIME), null);
    assert.equal(isRemoteInputGrantUsable({ ...activeGrant, status: "revoked", revoked_at: new Date(START_TIME + 1).toISOString() }, START_TIME), false);
    assert.equal(isRemoteInputGrantUsable({ ...activeGrant, id: "" }, START_TIME), false);
    assert.equal(isRemoteInputGrantUsable({ ...activeGrant, scope: "remote:view" }, START_TIME), false);
    assert.equal(remoteInputGrantRemainingText({ ...activeGrant, id: "" }, START_TIME), "未授权");
    assert.equal(remoteInputGrantRemainingText({ ...activeGrant, scope: "remote:view" }, START_TIME), "未授权");
    assert.equal(isRemoteInputGrantUsable(null, START_TIME), false);

    const nextGrant = { ...activeGrant, id: "rig_next", expires_at: new Date(EXPIRY_TIME + 60000).toISOString() };
    assert.deepEqual(reduceRemoteInputGrant(null, { type: "received", grant: activeGrant }, START_TIME), activeGrant);
    assert.deepEqual(reduceRemoteInputGrant(activeGrant, { type: "received", grant: nextGrant }, START_TIME), nextGrant);
    assert.equal(reduceRemoteInputGrant(activeGrant, { type: "received", grant: { ...activeGrant, status: "revoked" } }, START_TIME), null);
    assert.deepEqual(reduceRemoteInputGrant(activeGrant, { type: "received", grant: { ...nextGrant, status: "revoked" } }, START_TIME), activeGrant);
    assert.equal(reduceRemoteInputGrant(activeGrant, { type: "revoked", grantId: ACTIVE_GRANT_ID }, START_TIME), null);
    assert.deepEqual(reduceRemoteInputGrant(activeGrant, { type: "revoked", grantId: "rig_other" }, START_TIME), activeGrant);
    assert.equal(reduceRemoteInputGrant(activeGrant, { type: "expired", grantId: ACTIVE_GRANT_ID }, EXPIRY_TIME), null);
    assert.deepEqual(reduceRemoteInputGrant(activeGrant, { type: "expired", grantId: "rig_other" }, EXPIRY_TIME), activeGrant);
    assert.equal(reduceRemoteInputGrant(activeGrant, { type: "cleared" }, START_TIME), null);

    const remoteFrame = { width: 800, height: 450, originalWidth: 1600, originalHeight: 900 };
    assert.equal(JSON.stringify(mapViewerPointToRemote(400, 225, { width: 800, height: 450 }, remoteFrame)), JSON.stringify({ x: 800, y: 450 }));
    assert.equal(JSON.stringify(mapViewerPointToRemote(800, 450, { width: 800, height: 450 }, remoteFrame)), JSON.stringify({ x: 1599, y: 899 }));
    assert.equal(JSON.stringify(mapViewerPointToRemote(100, 225, { width: 1000, height: 450 }, remoteFrame)), JSON.stringify({ x: 0, y: 450 }));
    assert.equal(mapViewerPointToRemote(99, 225, { width: 1000, height: 450 }, remoteFrame), null);
    assert.equal(mapViewerPointToRemote(400, 225, { width: 0, height: 450 }, remoteFrame), null);

    const session = makeSession(client, server.origin);
    const lanSession = makeSession(client, "http://192.168.1.20:8000");
    const missingTokenSession = { ...session, token: "" };
    const expiredSession = { ...session, expiresAt: new Date(START_TIME - 1000).toISOString() };
    const screenInfo = client.remoteScreenWebSocketConnectionInfo(session);
    assert.equal(screenInfo.url, `${server.origin.replace("http:", "ws:")}/ws/remote/screen`);
    assertWebSocketTokenTransport(screenInfo, SESSION_TOKEN, { pathname: "/ws/remote/screen", label: "remote screen WebSocket" });
    assert.throws(() => client.remoteScreenWebSocketConnectionInfo(lanSession), assertInsecureLanError);
    assert.throws(() => client.remoteInputWebSocketConnectionInfo(lanSession, "input-token"), assertInsecureLanError);
    assert.throws(() => client.remoteScreenWebSocketConnectionInfo(missingTokenSession), assertAuthExpiredError);
    assert.throws(() => client.remoteInputWebSocketConnectionInfo(missingTokenSession, "input-token"), assertAuthExpiredError);
    assert.throws(() => client.remoteScreenWebSocketConnectionInfo(expiredSession), assertAuthExpiredError);
    assert.throws(() => client.remoteInputWebSocketConnectionInfo(expiredSession, "input-token"), assertAuthExpiredError);
    await assert.rejects(() => client.claimRemoteInputGrantToken(expiredSession, ACTIVE_GRANT_ID), assertAuthExpiredError);
    await assert.rejects(() => client.claimRemoteInputGrantToken(lanSession, ACTIVE_GRANT_ID), assertInsecureLanError);
    await assert.rejects(() => client.revokeRemoteInputGrant(lanSession, ACTIVE_GRANT_ID), assertInsecureLanError);
    assert.equal(server.requests.length, 0, "blocked insecure LAN remote input calls must not reach the smoke server");
    assert.throws(
      () => client.remoteInputWebSocketConnectionInfo(session, "bad token"),
      (error) => error.name === "ForbiddenError" && /WebSocket/.test(error.message),
    );

    const grantToken = await client.claimRemoteInputGrantToken(session, ACTIVE_GRANT_ID);
    assert.equal(server.requests.length, 1, "claiming a grant token must reach the local HTTP smoke service");
    assert.equal(server.requests[0].path, "/api/mobile/remote-input-grants/grant%2Fslash%20id/token");
    assert.equal(grantToken.token, "grant-token-active");
    assert.equal(grantToken.token_type, "Bearer");
    assert.equal(grantToken.grant_id, ACTIVE_GRANT_ID);
    assert.equal(grantToken.grant.scope, "remote:input");
    assert.equal(grantToken.expires_in, 300);

    const decidedApproval = await client.submitApprovalDecision(session, "approval-active", "approved");
    assert.equal(decidedApproval.id, "approval-active");
    assert.equal(decidedApproval.status, "approved");
    assert.equal(server.requests.length, 3);
    assert.equal(server.requests[1].method, "GET");
    assert.equal(server.requests[1].path, "/api/mobile/approvals/approval-active");
    assert.equal(server.requests[1].headers.authorization, `Bearer ${SESSION_TOKEN}`);
    assert.equal(server.requests[2].method, "POST");
    assert.equal(server.requests[2].path, "/api/mobile/approvals/approval-active/decision");
    assert.equal(server.requests[2].headers.authorization, "Bearer grant-token-active");
    assert.deepEqual(server.requests[2].json, { decision: "approved" });
    const otherDeviceSession = { ...session, deviceId: "device-2" };
    const pendingRemoteInputApproval = { ...state.approvals.get("approval-active"), status: "pending" };
    state.approvals.set("approval-active", pendingRemoteInputApproval);
    await assert.rejects(
      () => client.submitApprovalDecision(otherDeviceSession, "approval-active", "approved", {
        approval: pendingRemoteInputApproval,
        approvalType: "remote_input",
      }),
      (error) => error.name === "ForbiddenError" && /active remote input grant/i.test(error.message),
      "remote-input approval must not reuse a cached grant token from another paired mobile session",
    );
    assert.equal(server.requests.length, 3, "cross-session cached grant token rejection must fail before HTTP");
    client.clearRemoteInputGrantTokens();
    await assert.rejects(
      () => client.submitApprovalDecision(session, "approval-active", "approved"),
      (error) => error.name === "ForbiddenError" && /active remote input grant/i.test(error.message),
      "remote-input approval without a cached grant token must fail closed instead of using the generic mobile approval route",
    );
    assert.equal(server.requests.length, 4);
    assert.equal(server.requests[3].method, "GET");
    assert.equal(server.requests[3].path, "/api/mobile/approvals/approval-active");
    await assert.rejects(
      () => client.submitApprovalDecision(session, "approval-active", "approved", {
        approval: pendingRemoteInputApproval,
        approvalType: "remote_input",
      }),
      (error) => error.name === "ForbiddenError" && /active remote input grant/i.test(error.message),
      "clearing mobile session state must forget cached remote-input grant bearer tokens",
    );
    assert.equal(server.requests.length, 4, "cleared grant token cache must fail before a network decision request");
    const deniedWithoutGrant = await client.submitApprovalDecision(session, "approval-active", "denied", {
      approval: pendingRemoteInputApproval,
      approvalType: "remote_input",
    });
    assert.equal(deniedWithoutGrant.id, "approval-active");
    assert.equal(deniedWithoutGrant.status, "rejected");
    assert.equal(server.requests.length, 5);
    assert.equal(server.requests[4].method, "POST");
    assert.equal(server.requests[4].path, "/api/mobile/approvals/approval-active/reject");
    assert.equal(server.requests[4].headers.authorization, `Bearer ${SESSION_TOKEN}`);
    state.approvals.set("approval-active", pendingRemoteInputApproval);

    const explicitApprovalGrant = state.grants.get(APPROVAL_GRANT_ID);
    const explicitRemoteInputApproval = state.approvals.get("approval-explicit-grant");
    await assert.rejects(
      () => client.submitApprovalDecision(session, "approval-explicit-grant", "approved", {
        approvalType: "remote_input",
        remoteInputGrant: explicitApprovalGrant,
      }),
      (error) => error.name === "ForbiddenError" && /matching approval details/i.test(error.message),
      "explicit remote-input grants must require the matching approval details before claiming a token",
    );
    await assert.rejects(
      () => client.submitApprovalDecision(session, "approval-explicit-grant", "approved", {
        approval: { ...explicitRemoteInputApproval, source_device_id: "other-device" },
        approvalType: "remote_input",
        remoteInputGrant: explicitApprovalGrant,
      }),
      (error) => error.name === "ForbiddenError" && /mobile device/i.test(error.message),
      "remote-input approval submission must fail before HTTP when the approval device does not match the session",
    );
    await assert.rejects(
      () => client.submitApprovalDecision(session, "approval-explicit-grant", "denied", {
        approval: { ...explicitRemoteInputApproval, allowed_device_ids: ["other-device"] },
        approvalType: "remote_input",
        remoteInputGrant: explicitApprovalGrant,
      }),
      (error) => error.name === "ForbiddenError" && /not allowed/i.test(error.message),
      "remote-input approval rejection must fail before HTTP when approval details exclude this phone",
    );
    await assert.rejects(
      () => client.submitApprovalDecision(session, "approval-explicit-grant", "approved", {
        approval: { ...explicitRemoteInputApproval, source_grant_id: ACTIVE_GRANT_ID },
        approvalType: "remote_input",
        remoteInputGrant: explicitApprovalGrant,
      }),
      (error) => error.name === "ForbiddenError" && /active mobile grant/i.test(error.message),
      "remote-input approval submission must fail before HTTP when the active grant does not match the approval",
    );
    await assert.rejects(
      () => client.submitApprovalDecision(session, "approval-explicit-grant", "approved", {
        approval: explicitRemoteInputApproval,
        approvalType: "remote_input",
        remoteInputGrant: { ...explicitApprovalGrant, status: "revoked", revoked_at: new Date(START_TIME + 1).toISOString() },
      }),
      (error) => error.name === "ForbiddenError" && /active remote input grant/i.test(error.message),
      "remote-input approval submission must fail before HTTP when the active grant is already revoked",
    );
    assert.equal(server.requests.length, 5, "client-side remote-input binding failures must not reach the smoke server");
    const explicitDecision = await client.submitApprovalDecision(session, "approval-explicit-grant", "approved", {
      approval: explicitRemoteInputApproval,
      approvalType: "remote_input",
      remoteInputGrant: explicitApprovalGrant,
    });
    assert.equal(explicitDecision.id, "approval-explicit-grant");
    assert.equal(explicitDecision.status, "approved");
    assert.equal(server.requests.length, 7);
    assert.equal(server.requests[5].method, "POST");
    assert.equal(server.requests[5].path, "/api/mobile/remote-input-grants/grant-approval/token");
    assert.equal(server.requests[5].headers.authorization, `Bearer ${SESSION_TOKEN}`);
    assert.equal(server.requests[6].method, "POST");
    assert.equal(server.requests[6].path, "/api/mobile/approvals/approval-explicit-grant/decision");
    assert.equal(server.requests[6].headers.authorization, "Bearer grant-token-grant-approval");
    assert.deepEqual(server.requests[6].json, { decision: "approved" });

    const inputInfo = client.remoteInputWebSocketConnectionInfo(session, grantToken.token);
    assert.equal(inputInfo.url, `${server.origin.replace("http:", "ws:")}/ws/remote/input`);
    assertWebSocketTokenTransport(inputInfo, grantToken.token, { pathname: "/ws/remote/input", label: "remote input WebSocket" });
    assertAcceptedWebSocket(await connectWebSocket(inputInfo.url, inputInfo.protocols), inputInfo.protocols[0]);

    const wrongTokenHandshake = await connectWebSocket(inputInfo.url, [`${WS_PROTOCOL_PREFIX}wrong`]);
    assert.equal(wrongTokenHandshake.statusCode, 401);

    const revokedGrant = await client.revokeRemoteInputGrant(session, ACTIVE_GRANT_ID);
    assert.equal(server.requests.length, 8);
    assert.equal(server.requests[7].method, "DELETE");
    assert.equal(server.requests[7].path, "/api/mobile/remote-input-grants/grant%2Fslash%20id");
    assert.equal(revokedGrant.status, "revoked");
    assert.equal(reduceRemoteInputGrant(activeGrant, { type: "revoked", grantId: ACTIVE_GRANT_ID }, START_TIME), null);

    const rejectedAfterRevoke = await connectWebSocket(inputInfo.url, inputInfo.protocols);
    assert.equal(rejectedAfterRevoke.statusCode, 403);
    await assert.rejects(
      () => client.claimRemoteInputGrantToken(session, ACTIVE_GRANT_ID),
      (error) => error.name === "ForbiddenError" && /revoked/i.test(error.message),
    );

    state.now = EXPIRY_TIME - 1;
    const expiringToken = await client.claimRemoteInputGrantToken(session, EXPIRING_GRANT_ID);
    assert.equal(expiringToken.token, "grant-token-expiring");
    assert.equal(expiringToken.expires_in, 1);
    const expiringInfo = client.remoteInputWebSocketConnectionInfo(session, expiringToken.token);
    assertWebSocketTokenTransport(expiringInfo, expiringToken.token, { pathname: "/ws/remote/input", label: "expiring remote input WebSocket" });
    assertAcceptedWebSocket(await connectWebSocket(expiringInfo.url, expiringInfo.protocols), expiringInfo.protocols[0]);

    state.now = EXPIRY_TIME;
    assert.equal(isRemoteInputGrantUsable(expiringToken.grant, state.now), false);
    assert.equal(reduceRemoteInputGrant(expiringToken.grant, { type: "expired", grantId: EXPIRING_GRANT_ID }, state.now), null);
    const rejectedAfterExpire = await connectWebSocket(expiringInfo.url, expiringInfo.protocols);
    assert.equal(rejectedAfterExpire.statusCode, 410);
    await assert.rejects(
      () => client.claimRemoteInputGrantToken(session, EXPIRING_GRANT_ID),
      (error) => error.status === 410 && /expired/i.test(error.message),
    );
    state.now = START_TIME;
    await assert.rejects(
      () => client.claimRemoteInputGrantToken(session, INVALID_TOKEN_GRANT_ID),
      (error) => error.name === "ForbiddenError" && /WebSocket/.test(error.message),
    );

    const requestsBeforeOrdinaryDecision = server.requests.length;
    const ordinaryApproval = state.approvals.get("approval-ordinary");
    const ordinaryDecision = await client.submitApprovalDecision(session, "approval-ordinary", "approved", {
      approval: ordinaryApproval,
      remoteInputGrant: explicitApprovalGrant,
    });
    assert.equal(ordinaryDecision.id, "approval-ordinary");
    assert.equal(ordinaryDecision.status, "approved");
    assert.equal(
      server.requests.length,
      requestsBeforeOrdinaryDecision + 1,
      "ordinary approvals must not claim a remote-input grant token when a grant is active",
    );
    assert.equal(server.requests.at(-1).method, "POST");
    assert.equal(server.requests.at(-1).path, "/api/mobile/approvals/approval-ordinary/approve");
    assert.equal(server.requests.at(-1).headers.authorization, `Bearer ${SESSION_TOKEN}`);

    assert.equal(server.upgrades.length, 5);
    assert.equal(server.upgrades.filter((upgrade) => upgrade.accepted).length, 2);
  } finally {
    await server.close();
  }
}

main()
  .then(() => console.log("remote input grant behavior smoke passed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
