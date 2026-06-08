const assert = require("node:assert/strict");

const {
  acceptWebSocketUpgrade,
  assertAcceptedWebSocket,
  assertJsonRequest,
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
const EXPIRING_GRANT_ID = "grant-expiring";
const START_TIME = Date.now();
const EXPIRY_TIME = START_TIME + 5 * 60 * 1000;

function makeGrant(id, overrides = {}) {
  return {
    id,
    status: "active",
    scope: "remote:input",
    created_at: new Date(START_TIME).toISOString(),
    expires_at: new Date(EXPIRY_TIME).toISOString(),
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

async function main() {
  const client = loadMobileClient();
  const {
    isRemoteInputGrantUsable,
    mapViewerPointToRemote,
    reduceRemoteInputGrant,
    remoteInputGrantExpiryDelayMs,
  } = loadTsModule(mobilePath("src/remoteInputGrant.ts"));

  const state = {
    now: START_TIME,
    grants: new Map([
      [ACTIVE_GRANT_ID, makeGrant(ACTIVE_GRANT_ID)],
      [EXPIRING_GRANT_ID, makeGrant(EXPIRING_GRANT_ID)],
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
          source_device_id: DEVICE_ID,
          source_grant_id: ACTIVE_GRANT_ID,
          allowed_device_ids: [DEVICE_ID],
          required_mobile_scopes: ["remote:input"],
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
        const grantId = approval.source_grant_id;
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

      return false;
    },
    handleUpgrade: ({ req, socket, url, upgrade }) => {
      if (url.pathname !== "/ws/remote/input") {
        rejectWebSocketUpgrade(socket, 404, "Unknown WebSocket path");
        return;
      }
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

    const inputInfo = client.remoteInputWebSocketConnectionInfo(session, grantToken.token);
    assert.equal(inputInfo.url, `${server.origin.replace("http:", "ws:")}/ws/remote/input`);
    assert.equal(JSON.stringify(inputInfo.protocols), JSON.stringify([`${WS_PROTOCOL_PREFIX}${grantToken.token}`]));
    assert.doesNotMatch(inputInfo.url, /grant-token-active|token=/);
    assertAcceptedWebSocket(await connectWebSocket(inputInfo.url, inputInfo.protocols), inputInfo.protocols[0]);

    const wrongTokenHandshake = await connectWebSocket(inputInfo.url, [`${WS_PROTOCOL_PREFIX}wrong`]);
    assert.equal(wrongTokenHandshake.statusCode, 401);

    const revokedGrant = await client.revokeRemoteInputGrant(session, ACTIVE_GRANT_ID);
    assert.equal(server.requests.length, 4);
    assert.equal(server.requests[3].method, "DELETE");
    assert.equal(server.requests[3].path, "/api/mobile/remote-input-grants/grant%2Fslash%20id");
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
