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

function loadTsModule(modulePath, sandboxOverrides = {}) {
  const source = fs.readFileSync(modulePath, "utf8");
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
    URL,
    ...sandboxOverrides,
  };
  sandbox.exports = sandbox.module.exports;
  vm.runInNewContext(compiled, sandbox, { filename: modulePath });
  return sandbox.module.exports;
}

function makeSession(client, baseUrl, token = "session-token") {
  const security = client.describeBaseUrlSecurity(baseUrl);
  return {
    baseUrl: security.normalizedBaseUrl,
    baseUrlSecurity: security,
    deviceId: "device-1",
    token,
  };
}

async function main() {
  const clientSource = fs.readFileSync(clientSourcePath, "utf8");
  const approvalsSource = fs.readFileSync(approvalsSourcePath, "utf8");
  const remoteScreenSource = fs.readFileSync(remoteScreenSourcePath, "utf8");
  const notificationsSource = fs.readFileSync(notificationsSourcePath, "utf8");
  const fetchCalls = [];
  const client = loadTsModule(clientSourcePath, {
    fetch: async (url, init = {}) => {
      fetchCalls.push({ url: String(url), init });
      if (String(url).endsWith("/token")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            token: "grant-token",
            token_type: "Bearer",
            grant_id: "grant/slash id",
            device_id: "device-1",
            expires_at: "2026-06-01T00:05:00.000Z",
            expires_in: 300,
            grant: activeGrant,
          }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ...activeGrant,
          status: "revoked",
          revoked_at: "2026-06-01T00:02:00.000Z",
        }),
      };
    },
  });
  const {
    isRemoteInputGrantUsable,
    mapViewerPointToRemote,
    reduceRemoteInputGrant,
    remoteInputGrantExpiryDelayMs,
    remoteInputGrantRemainingText,
  } = loadTsModule(sourcePath);

  const now = Date.parse("2026-06-01T00:00:00.000Z");
  const activeGrant = {
    id: "grant/slash id",
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
  assert.deepEqual(reduceRemoteInputGrant(null, { type: "received", grant: activeGrant }, now), activeGrant);
  assert.deepEqual(reduceRemoteInputGrant(activeGrant, { type: "received", grant: nextGrant }, now), nextGrant);
  assert.equal(reduceRemoteInputGrant(activeGrant, { type: "received", grant: { ...activeGrant, status: "revoked" } }, now), null);
  assert.deepEqual(reduceRemoteInputGrant(activeGrant, { type: "received", grant: { ...nextGrant, status: "revoked" } }, now), activeGrant);
  assert.equal(reduceRemoteInputGrant(activeGrant, { type: "revoked", grantId: "grant/slash id" }, now), null);
  assert.deepEqual(reduceRemoteInputGrant(activeGrant, { type: "revoked", grantId: "rig_other" }, now), activeGrant);
  assert.equal(reduceRemoteInputGrant(activeGrant, { type: "expired", grantId: "grant/slash id" }, now), null);
  assert.deepEqual(reduceRemoteInputGrant(activeGrant, { type: "expired", grantId: "rig_other" }, now), activeGrant);
  assert.equal(reduceRemoteInputGrant(activeGrant, { type: "cleared" }, now), null);

  const remoteFrame = { width: 800, height: 450, originalWidth: 1600, originalHeight: 900 };
  assert.equal(JSON.stringify(mapViewerPointToRemote(400, 225, { width: 800, height: 450 }, remoteFrame)), JSON.stringify({ x: 800, y: 450 }));
  assert.equal(JSON.stringify(mapViewerPointToRemote(800, 450, { width: 800, height: 450 }, remoteFrame)), JSON.stringify({ x: 1599, y: 899 }));
  assert.equal(JSON.stringify(mapViewerPointToRemote(100, 225, { width: 1000, height: 450 }, remoteFrame)), JSON.stringify({ x: 0, y: 450 }));
  assert.equal(mapViewerPointToRemote(99, 225, { width: 1000, height: 450 }, remoteFrame), null);
  assert.equal(mapViewerPointToRemote(400, 225, { width: 0, height: 450 }, remoteFrame), null);

  const lanSession = makeSession(client, "http://192.168.1.20:8000");
  assert.equal(lanSession.baseUrlSecurity.kind, "insecureLan");
  const inputInfo = client.remoteInputWebSocketConnectionInfo(lanSession, "grant-token");
  assert.equal(inputInfo.url, "ws://192.168.1.20:8000/ws/remote/input");
  assert.equal(JSON.stringify(inputInfo.protocols), JSON.stringify(["lengrvis.mobile.token.grant-token"]));
  assert.match(inputInfo.warning, /明文传输/);
  assert.doesNotMatch(inputInfo.url, /grant-token|token=/);

  const httpsSession = makeSession(client, "https://example.test:8443");
  assert.equal(client.remoteInputWebSocketConnectionInfo(httpsSession, "grant-token").url, "wss://example.test:8443/ws/remote/input");
  assert.doesNotMatch(client.remoteInputWebSocketConnectionInfo(httpsSession, "grant-token").url, /grant-token|token=/);

  const tlsTrustMetadata = {
    transport: { http_scheme: "https", websocket_scheme: "wss", tls_enabled: true },
    tls: {
      enabled: true,
      trust_status: "requires_trust",
      requires_trust: true,
      self_signed: true,
      fingerprint_sha256: "11223344556677889900aabbccddeeff",
    },
  };
  const tlsTrustSecurity = client.describeBaseUrlSecurity("https://example.test:8443", tlsTrustMetadata);
  const tlsTrustSession = {
    baseUrl: tlsTrustSecurity.normalizedBaseUrl,
    baseUrlSecurity: tlsTrustSecurity,
    security: tlsTrustSecurity.backendSecurity,
    deviceId: "device-1",
    token: "session-token",
  };
  const tlsTrustScreenInfo = client.remoteScreenWebSocketConnectionInfo(tlsTrustSession);
  assert.equal(tlsTrustScreenInfo.url, "wss://example.test:8443/ws/remote/screen");
  assert.equal(JSON.stringify(tlsTrustScreenInfo.protocols), JSON.stringify(["lengrvis.mobile.token.session-token"]));
  assert.equal(tlsTrustScreenInfo.security.requiresTlsTrust, true);
  assert.match(tlsTrustScreenInfo.warning, /证书|自签/);
  assert.doesNotMatch(tlsTrustScreenInfo.url, /session-token|token=/);

  const grantToken = await client.claimRemoteInputGrantToken(lanSession, "grant/slash id");
  assert.equal(fetchCalls[0].url, "http://192.168.1.20:8000/api/mobile/remote-input-grants/grant%2Fslash%20id/token");
  assert.equal(fetchCalls[0].init.method, "POST");
  assert.equal(fetchCalls[0].init.headers.Authorization, "Bearer session-token");
  assert.equal(grantToken.token_type, "Bearer");
  assert.equal(grantToken.grant_id, "grant/slash id");
  assert.equal(grantToken.grant.scope, "remote:input");
  assert.equal(grantToken.expires_in, 300);

  const revokedGrant = await client.revokeRemoteInputGrant(lanSession, "grant/slash id");
  assert.equal(fetchCalls[1].url, "http://192.168.1.20:8000/api/mobile/remote-input-grants/grant%2Fslash%20id");
  assert.equal(fetchCalls[1].init.method, "DELETE");
  assert.equal(fetchCalls[1].init.headers.Authorization, "Bearer session-token");
  assert.equal(revokedGrant.status, "revoked");
  assert.equal(revokedGrant.revoked_at, "2026-06-01T00:02:00.000Z");

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
  assert.match(remoteScreenSource, /remoteInputWebSocketConnectionInfo\(session, grantToken\.token\)/);
  assert.match(remoteScreenSource, /transportWarning/);
  assert.match(remoteScreenSource, /remoteTransportNotice/);
  assert.match(remoteScreenSource, /token 通过 WebSocket protocol 发送，不写入 URL/);
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
}

main()
  .then(() => console.log("remote input grant smoke passed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
