import type { ApprovalDetail, BackendApproval, BackendWakeup, MobileTask, MobileTaskLaunchResult, MobileTaskMode, MobileTaskTemplateId, PairingSession, RemoteInputGrant, RemoteInputGrantToken } from "./types";
import { AuthExpiredError, BackendHttpError, ForbiddenError, InsecureLanBaseUrlError, authHeaders, fetchWithTimeout, jsonAuthHeaders, parseJson, parseRemoteInputGrantJson } from "./http";
import { assertSafePairingSession, assertWebSocketSubprotocolToken, describeBaseUrlSecurity, mergeBaseUrlSecurityMetadata, normalizePairingSecurityMetadata, normalizePairingServerInfo, sessionHasUnsafeRemoteTransport, validatePairResult } from "./security";
import { configureNativeTlsTrust } from "./nativeTlsTrust";
import { REMOTE_INPUT_SCOPE } from "./types";

const remoteInputGrantTokens = new Map<string, RemoteInputGrantToken>();


export async function pairWithBackend(
  baseUrl: string,
  code: string,
  deviceName: string,
  pairingMetadata?: unknown,
): Promise<PairingSession> {
  const baseUrlSecurity = describeBaseUrlSecurity(baseUrl, pairingMetadata);
  if (baseUrlSecurity.isInsecureLan || sessionHasUnsafeRemoteTransport(baseUrlSecurity)) {
    throw new InsecureLanBaseUrlError(baseUrlSecurity);
  }
  await configureNativeTlsTrust(baseUrlSecurity);
  const normalizedBaseUrl = baseUrlSecurity.normalizedBaseUrl;
  const response = await fetchWithTimeout(`${normalizedBaseUrl}/api/pair/confirm`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ code, device_name: deviceName }),
  });
  const { payload, expiresAt } = validatePairResult(await parseJson<unknown>(response));
  const pairingSecurity = normalizePairingSecurityMetadata(payload, baseUrlSecurity);
  const mergedBaseUrlSecurity = mergeBaseUrlSecurityMetadata(baseUrlSecurity, pairingSecurity);
  if (mergedBaseUrlSecurity.isInsecureLan || sessionHasUnsafeRemoteTransport(mergedBaseUrlSecurity)) {
    throw new InsecureLanBaseUrlError(mergedBaseUrlSecurity);
  }
  await configureNativeTlsTrust(mergedBaseUrlSecurity);
  return {
    baseUrl: normalizedBaseUrl,
    token: payload.token,
    deviceId: payload.device_id,
    expiresAt,
    baseUrlSecurity: mergedBaseUrlSecurity,
    server: normalizePairingServerInfo(payload.server),
    security: pairingSecurity,
  };
}

export async function listPendingApprovals(session: PairingSession): Promise<BackendApproval[]> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/approvals/pending`, {
    headers: authHeaders(safeSession.token),
  });
  return parseJson<BackendApproval[]>(response);
}

export async function getApprovalDetail(session: PairingSession, approvalId: string): Promise<ApprovalDetail> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}`, {
    headers: authHeaders(safeSession.token),
  });
  return parseJson<ApprovalDetail>(response);
}

export async function submitApprovalDecision(
  session: PairingSession,
  approvalId: string,
  decision: "approved" | "denied",
  options: { approval?: BackendApproval; approvalType?: string; remoteInputGrant?: RemoteInputGrant | null } = {},
): Promise<BackendApproval> {
  const safeSession = await safeNetworkSession(session);
  if (decision === "approved") {
    const remoteInputGrantToken = await remoteInputGrantTokenForApproval(safeSession, approvalId, options);
    if (remoteInputGrantToken) {
      const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}/decision`, {
        method: "POST",
        headers: jsonAuthHeaders(remoteInputGrantToken.token),
        body: JSON.stringify({ decision }),
      });
      try {
        return await parseRemoteInputGrantJson<BackendApproval>(response, "use");
      } catch (error) {
        forgetRemoteInputGrantToken(remoteInputGrantToken.grant_id || remoteInputGrantToken.grant?.id || "");
        forgetRemoteInputGrantToken(remoteInputGrantToken.grant?.binding_ref || "");
        throw error;
      }
    }
  } else if (options.approval && isRemoteInputApproval(options.approval)) {
    assertRemoteInputApprovalRejectAllowedForSession(safeSession, options.approval);
  }
  const action = decision === "approved" ? "approve" : "reject";
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}/${action}`, {
    method: "POST",
    headers: authHeaders(safeSession.token),
  });
  return parseJson<BackendApproval>(response);
}

export async function disconnectMobileDevice(session: PairingSession): Promise<void> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/devices/${encodeURIComponent(safeSession.deviceId)}`, {
    method: "DELETE",
    headers: authHeaders(safeSession.token),
  });
  await parseJson<unknown>(response);
}

export async function listMobileTasks(session: PairingSession): Promise<MobileTask[]> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/tasks`, {
    headers: authHeaders(safeSession.token),
  });
  const payload = await parseJson<{ tasks: MobileTask[] }>(response);
  return payload.tasks;
}

export async function listPendingMobileWakeups(session: PairingSession): Promise<BackendWakeup[]> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/wakeups/pending`, {
    headers: authHeaders(safeSession.token),
  });
  return parseJson<BackendWakeup[]>(response);
}

export async function approveMobileWakeup(session: PairingSession, wakeupId: string): Promise<BackendWakeup> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/wakeups/${encodeURIComponent(wakeupId)}/approve`, {
    method: "POST",
    headers: authHeaders(safeSession.token),
  });
  return parseJson<BackendWakeup>(response);
}

export async function rejectMobileWakeup(session: PairingSession, wakeupId: string): Promise<BackendWakeup> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/wakeups/${encodeURIComponent(wakeupId)}/reject`, {
    method: "POST",
    headers: authHeaders(safeSession.token),
  });
  return parseJson<BackendWakeup>(response);
}

export async function createMobileTask(
  session: PairingSession,
  request: { template_id: MobileTaskTemplateId; user_input?: string; mode: MobileTaskMode },
): Promise<MobileTaskLaunchResult> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/tasks`, {
    method: "POST",
    headers: jsonAuthHeaders(safeSession.token),
    body: JSON.stringify(request),
  });
  return parseJson<MobileTaskLaunchResult>(response);
}

export async function submitMobileTaskFollowUp(
  session: PairingSession,
  taskId: string,
  request: { instruction: string; mode?: MobileTaskMode },
): Promise<MobileTaskLaunchResult> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/tasks/${encodeURIComponent(taskId)}/follow-up`, {
    method: "POST",
    headers: jsonAuthHeaders(safeSession.token),
    body: JSON.stringify(request),
  });
  return parseJson<MobileTaskLaunchResult>(response);
}

export async function submitMobileTaskCommand(
  session: PairingSession,
  taskId: string,
  command: "pause" | "resume" | "cancel",
): Promise<MobileTask> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/tasks/${encodeURIComponent(taskId)}/${command}`, {
    method: "POST",
    headers: authHeaders(safeSession.token),
  });
  return parseJson<MobileTask>(response);
}

export async function claimRemoteInputGrantToken(session: PairingSession, grantId: string): Promise<RemoteInputGrantToken> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/remote-input-grants/${encodeURIComponent(grantId)}/token`, {
    method: "POST",
    headers: authHeaders(safeSession.token),
  });
  const payload = validateRemoteInputGrantToken(
    await parseRemoteInputGrantJson<RemoteInputGrantToken>(response, "claim"),
    safeSession,
    grantId,
  );
  rememberRemoteInputGrantToken(payload);
  return payload;
}

export async function revokeRemoteInputGrant(session: PairingSession, grantId: string): Promise<RemoteInputGrant> {
  const safeSession = await safeNetworkSession(session);
  const response = await fetchWithTimeout(`${safeSession.baseUrl}/api/mobile/remote-input-grants/${encodeURIComponent(grantId)}`, {
    method: "DELETE",
    headers: authHeaders(safeSession.token),
  });
  const payload = await parseJson<RemoteInputGrant>(response);
  forgetRemoteInputGrantToken(grantId);
  forgetRemoteInputGrantToken(payload.id);
  forgetRemoteInputGrantToken(payload.binding_ref || "");
  return payload;
}

export function clearRemoteInputGrantTokens(): void {
  remoteInputGrantTokens.clear();
}

export async function remoteInputGrantTokenForApproval(
  session: PairingSession,
  approvalId: string,
  options: { approval?: BackendApproval; approvalType?: string; remoteInputGrant?: RemoteInputGrant | null },
): Promise<RemoteInputGrantToken | null> {
  const explicitGrantId = options.remoteInputGrant?.id ?? "";
  if (explicitGrantId) {
    if (options.approval && !isRemoteInputApproval(options.approval)) {
      return null;
    }
    if (!options.approval) {
      throw new ForbiddenError("Remote input approval requires the matching approval details.");
    }
    if (!isRemoteInputGrantUsable(options.remoteInputGrant)) {
      throw new ForbiddenError("Remote input approval requires an active remote input grant.");
    }
    assertRemoteInputApprovalMatchesSession(session, options.approval, explicitGrantId, options.remoteInputGrant);
    return usableRemoteInputGrantTokenForSession(explicitGrantId, session) ?? claimRemoteInputGrantToken(session, explicitGrantId);
  }

  let approval = options.approval;
  if (!approval) {
    try {
      approval = (await getApprovalDetail(session, approvalId)).approval;
    } catch (error) {
      if (error instanceof AuthExpiredError) throw error;
    }
  }

  const explicitRemoteInput = options.approvalType === "remote_input";
  if (!approval && !explicitRemoteInput) return null;
  if (approval && !isRemoteInputApproval(approval) && !explicitRemoteInput) return null;

  const grantToken = approval ? usableRemoteInputGrantTokenForApproval(approval, session) : null;
  const grantId = grantToken?.grant_id || grantToken?.grant?.id || (approval ? remoteInputApprovalGrantId(approval) : "");
  const grant = grantToken?.grant ?? null;
  if (approval && isRemoteInputApproval(approval) && approval.remote_input_binding?.binding_ref && !grantToken) {
    throw new ForbiddenError("Remote input approval requires an active remote input grant.");
  }
  if (approval && isRemoteInputApproval(approval)) {
    assertRemoteInputApprovalMatchesSession(session, approval, grantId, grant);
  }
  if (!grantId) {
    throw new ForbiddenError("Remote input approval requires an active remote input grant.");
  }
  if (!grantToken) {
    throw new ForbiddenError("Remote input approval requires an active remote input grant.");
  }
  return grantToken;
}

export function rememberRemoteInputGrantToken(payload: RemoteInputGrantToken): void {
  const grantId = payload.grant_id || payload.grant?.id || "";
  if (!grantId) return;
  if (!isRemoteInputGrantTokenUsable(payload)) {
    remoteInputGrantTokens.delete(grantId);
    forgetRemoteInputGrantToken(payload.grant?.binding_ref || "");
    return;
  }
  remoteInputGrantTokens.set(grantId, payload);
  const bindingRef = payload.grant?.binding_ref || "";
  if (bindingRef) remoteInputGrantTokens.set(bindingRef, payload);
}

export function forgetRemoteInputGrantToken(grantId: string): void {
  if (grantId) remoteInputGrantTokens.delete(grantId);
}

export function usableRemoteInputGrantToken(grantId: string): RemoteInputGrantToken | null {
  const cached = remoteInputGrantTokens.get(grantId);
  if (!cached) return null;
  if (!isRemoteInputGrantTokenUsable(cached)) {
    remoteInputGrantTokens.delete(grantId);
    return null;
  }
  return cached;
}

export function usableRemoteInputGrantTokenForApproval(approval: BackendApproval, session: PairingSession): RemoteInputGrantToken | null {
  const grantId = remoteInputApprovalGrantId(approval);
  if (grantId) return usableRemoteInputGrantTokenForSession(grantId, session);
  const bindingRef = approval.remote_input_binding?.binding_ref || "";
  if (!bindingRef) return null;
  const cached = usableRemoteInputGrantTokenForSession(bindingRef, session);
  if (cached && cached.grant?.binding_ref === bindingRef) return cached;
  return null;
}

export function usableRemoteInputGrantTokenForSession(grantId: string, session: PairingSession): RemoteInputGrantToken | null {
  const cached = usableRemoteInputGrantToken(grantId);
  if (!cached) return null;
  if (cached.device_id && cached.device_id !== session.deviceId) {
    forgetRemoteInputGrantToken(grantId);
    forgetRemoteInputGrantToken(cached.grant_id || cached.grant?.id || "");
    forgetRemoteInputGrantToken(cached.grant?.binding_ref || "");
    return null;
  }
  return cached;
}

export function validateRemoteInputGrantToken(
  payload: RemoteInputGrantToken,
  session: PairingSession,
  requestedGrantId: string,
): RemoteInputGrantToken {
  const grantId = payload.grant_id || payload.grant?.id || "";
  if (!payload.token || payload.token_type !== "Bearer") {
    throw new ForbiddenError("Remote input grant token response is missing a bearer token.");
  }
  if (grantId !== requestedGrantId) {
    throw new ForbiddenError("Remote input grant token does not match the requested grant.");
  }
  if (payload.device_id && payload.device_id !== session.deviceId) {
    throw new ForbiddenError("Remote input grant token belongs to a different mobile device.");
  }
  if (payload.grant && !isRemoteInputGrantUsable(payload.grant)) {
    throw new BackendHttpError(410, "Remote input grant is expired or revoked.");
  }
  if (!isRemoteInputGrantTokenUsable(payload)) {
    throw new BackendHttpError(410, "Remote input grant token is expired or revoked.");
  }
  assertWebSocketSubprotocolToken(payload.token);
  return payload;
}

export function isRemoteInputGrantTokenUsable(payload: RemoteInputGrantToken, nowMs = Date.now()): boolean {
  if (!payload.token || payload.token_type !== "Bearer") return false;
  if (payload.grant && !isRemoteInputGrantUsable(payload.grant, nowMs)) return false;
  const expiresAt = Date.parse(payload.expires_at || payload.grant?.expires_at || "");
  if (!Number.isFinite(expiresAt) || expiresAt <= nowMs) return false;
  if (Number.isFinite(payload.expires_in) && payload.expires_in <= 0) return false;
  return true;
}

export function isRemoteInputGrantUsable(grant: RemoteInputGrant | null | undefined, nowMs = Date.now()): grant is RemoteInputGrant {
  if (!grant) return false;
  if (normalizedRemoteInputGrantText(grant.scope) !== REMOTE_INPUT_SCOPE) return false;
  if (normalizedRemoteInputGrantText(grant.status) !== "active") return false;
  if (normalizedRemoteInputGrantText(grant.revoked_at)) return false;
  const expiresAt = Date.parse(grant.expires_at || "");
  return Number.isFinite(expiresAt) && expiresAt > nowMs;
}

export function normalizedRemoteInputGrantText(value: string | undefined): string {
  return String(value ?? "").trim().toLowerCase();
}

async function safeNetworkSession(session: PairingSession): Promise<PairingSession> {
  const safeSession = assertSafePairingSession(session);
  await configureNativeTlsTrust(safeSession.baseUrlSecurity);
  return safeSession;
}

export function isRemoteInputApproval(approval: BackendApproval): boolean {
  return (
    approval.approval_type === "remote_input" ||
    approval.source === "remote_input" ||
    approval.required_mobile_scopes?.includes(REMOTE_INPUT_SCOPE) === true
  );
}

export function remoteInputApprovalGrantId(approval: BackendApproval): string {
  return approval.source_grant_id ?? "";
}

export function assertRemoteInputApprovalMatchesSession(
  session: PairingSession,
  approval: BackendApproval,
  grantId: string,
  grant?: RemoteInputGrant | null,
): void {
  const binding = approval.remote_input_binding;
  const sourceDeviceId = approval.source_device_id ?? "";
  const sourceGrantId = approval.source_grant_id ?? "";
  if (!sourceDeviceId && binding?.device_bound !== true) {
    throw new ForbiddenError("Remote input approval does not match this mobile device.");
  }
  if (sourceDeviceId && sourceDeviceId !== session.deviceId) {
    throw new ForbiddenError("Remote input approval does not match this mobile device.");
  }
  const allowedDevices = approval.allowed_device_ids ?? [];
  if (allowedDevices.length > 0 && !allowedDevices.includes(session.deviceId)) {
    throw new ForbiddenError("Remote input approval is not allowed for this mobile device.");
  }
  if (binding?.matches_current_device === false) {
    throw new ForbiddenError("Remote input approval does not match this mobile device.");
  }
  if (!sourceGrantId && binding?.grant_bound !== true) {
    throw new ForbiddenError("Remote input approval does not match the active mobile grant.");
  }
  if (sourceGrantId && sourceGrantId !== grantId) {
    throw new ForbiddenError("Remote input approval does not match the active mobile grant.");
  }
  if (!sourceGrantId) {
    if (binding?.binding_ref) {
      if (!grant?.binding_ref || grant.binding_ref !== binding.binding_ref) {
        throw new ForbiddenError("Remote input approval does not match the active mobile grant.");
      }
    } else if (binding?.matches_current_grant !== true) {
      throw new ForbiddenError("Remote input approval does not match the active mobile grant.");
    }
  }
  if (binding?.matches_current_grant === false) {
    throw new ForbiddenError("Remote input approval does not match the active mobile grant.");
  }
  if (binding && binding.requires_remote_input_scope !== true) {
    throw new ForbiddenError("Remote input approval requires an active remote input grant.");
  }
}

export function assertRemoteInputApprovalRejectAllowedForSession(session: PairingSession, approval: BackendApproval): void {
  const binding = approval.remote_input_binding;
  const sourceDeviceId = approval.source_device_id ?? "";
  if (sourceDeviceId && sourceDeviceId !== session.deviceId) {
    throw new ForbiddenError("Remote input approval does not match this mobile device.");
  }
  const allowedDevices = approval.allowed_device_ids ?? [];
  if (allowedDevices.length > 0 && !allowedDevices.includes(session.deviceId)) {
    throw new ForbiddenError("Remote input approval is not allowed for this mobile device.");
  }
  if (binding?.matches_current_device === false) {
    throw new ForbiddenError("Remote input approval does not match this mobile device.");
  }
  if (!sourceDeviceId && allowedDevices.length === 0 && binding?.device_bound !== true) {
    throw new ForbiddenError("Remote input approval does not match this mobile device.");
  }
}
