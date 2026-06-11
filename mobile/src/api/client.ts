export interface PairResult {
  token: string;
  token_type: "Bearer";
  device_id: string;
  expires_in: number;
  expires_at?: string;
  expiresAt?: string;
  server?: {
    host: string;
    port: number;
    protocol?: string;
    scheme?: string;
    url?: string;
    base_url?: string;
    origin?: string;
    transport_security?: unknown;
  };
  security?: unknown;
  transport?: unknown;
  transport_security?: unknown;
  https_enabled?: boolean;
  trust_required?: boolean;
  server_origin?: string;
}

export interface BackendApproval {
  id: string;
  task_id: string;
  step_id?: string | null;
  approval_type: string;
  message: string;
  diff_preview: unknown;
  tool_name?: string;
  risk_level?: string;
  tool_trust_tier?: string;
  tool_effects?: string[];
  resource_kinds?: string[];
  policy_mode?: string;
  permission_mode?: string;
  permission_policy_version?: string;
  dry_run_summary?: string;
  model_action?: unknown;
  runtime_control_fields?: unknown;
  runtime_fields?: unknown;
  engineering_boundary?: unknown;
  status: "pending" | "approved" | "rejected" | "expired";
  created_at: string;
  decided_at?: string | null;
  source?: string;
  source_device_id?: string;
  source_grant_id?: string;
  allowed_device_ids?: string[];
  required_mobile_scopes?: string[];
  remote_input_binding?: RemoteInputBinding;
}

export interface RemoteInputBinding {
  device_bound?: boolean;
  grant_bound?: boolean;
  requires_remote_input_scope?: boolean;
  binding_ref?: string;
  matches_current_device?: boolean;
  matches_current_grant?: boolean;
}

export interface BackendTask {
  id: string;
  user_goal: string;
  status: string;
  mode: string;
  final_summary: string;
  created_at: string;
  updated_at: string;
}

export interface MobileTask {
  id: string;
  title: string;
  status: string;
  status_label?: string;
  status_detail?: string;
  mode: string;
  summary: string;
  available_actions?: MobileTaskAction[];
  can_pause?: boolean;
  can_resume?: boolean;
  can_cancel?: boolean;
  can_follow_up?: boolean;
  is_terminal?: boolean;
  content_redacted?: boolean;
  privacy_redacted?: boolean;
  result_verified?: boolean;
  evidence_verified?: boolean;
  completion_evidence?: MobileTaskCompletionEvidence;
  credibility?: "unverified" | "partial" | "verified" | "redacted" | string;
  created_at: string;
  updated_at: string;
}

export type MobileTaskAction = "pause" | "resume" | "cancel" | "follow_up";

export interface MobileTaskCompletionEvidence {
  level?: "submission" | "task_created" | "visible_progress" | "completed_result" | "safe_failure" | string;
  result_verified?: boolean;
  signoff?: boolean;
  missing_count?: number;
}

export type MobileTaskTemplateId =
  | "organize_downloads"
  | "summarize_local_docs"
  | "find_large_files"
  | "check_computer_status"
  | "document_qa";

export type MobileTaskMode = "efficiency" | "privacy" | "hybrid";

export interface MobileTaskLaunchResult {
  task: MobileTask;
  message: string;
  source_task_id?: string;
}

export interface BackendPlanStep {
  id: string;
  order: number;
  agent_name: string;
  tool_name: string;
  description: string;
  status: string;
  risk_level?: string;
  requires_approval: boolean;
  tool_effects?: string[];
  resource_kinds?: string[];
  trust_tier?: string;
  deferred_tool?: boolean;
  args?: Record<string, unknown>;
  expected_observation?: string;
}

export interface BackendPlan {
  id: string;
  goal: string;
  assumptions?: string[];
  steps: BackendPlanStep[];
}

export interface ApprovalDetail {
  approval: BackendApproval;
  task: BackendTask | null;
  plan: BackendPlan | null;
  preview: unknown;
}

export type ApprovalEvent =
  | { type: "connected"; device_id?: string; pending: BackendApproval[]; remote_input_grants?: RemoteInputGrant[]; tasks?: MobileTask[] }
  | { type: "heartbeat" }
  | { type: "approval_notification"; approval: BackendApproval }
  | { type: "approval_created"; approval: BackendApproval }
  | { type: "approval_decided"; approval: BackendApproval }
  | { type: "remote_input_grant_created"; device_id: string; grant: RemoteInputGrant }
  | { type: "remote_input_grant_revoked"; device_id: string; grant: RemoteInputGrant }
  | { type: "mobile_device_revoked"; device_id: string; device: MobileDevice };

export type RemoteScreenEvent =
  | { type: "connected"; fps: number; quality: number }
  | {
      type: "frame";
      sequence: number;
      image: string;
      timestamp: string;
      width: number;
      height: number;
      original_width: number;
      original_height: number;
      screen_origin_x?: number;
      screen_origin_y?: number;
    }
  | { type: "error"; message: string };

export interface PairingSession {
  baseUrl: string;
  token: string;
  deviceId: string;
  expiresAt?: string;
  baseUrlSecurity: BaseUrlSecurity;
  server?: PairingServerInfo;
  security?: PairingSecurityMetadata;
}

export interface PairingServerInfo {
  host: string;
  port: number;
  protocol?: string;
  scheme?: string;
  url?: string;
  baseUrl?: string;
  origin?: string;
  transportSecurity?: PairingSecurityMetadata;
}

export interface RemoteInputGrant {
  id: string;
  status: string;
  scope: "remote:input";
  created_at: string;
  expires_at: string;
  revoked_at?: string;
  binding_ref?: string;
}

export interface RemoteInputGrantToken {
  token: string;
  token_type: "Bearer";
  grant_id: string;
  device_id: string;
  expires_at: string;
  expires_in: number;
  grant: RemoteInputGrant;
}

export interface MobileDevice {
  device_id: string;
  device_name: string;
  status: string;
  revoked_at?: string;
  updated_at?: string;
}

export type BaseUrlSecurityKind = "https" | "loopbackHttp" | "insecureLan";

export type WebSocketProtocol = "ws:" | "wss:";

export interface PairingTransportMetadata {
  httpScheme?: "http" | "https" | string;
  webSocketScheme?: "ws" | "wss" | string;
  tlsEnabled?: boolean;
  advertisedBaseUrl?: string;
  serverUrl?: string;
}

export type ServerTlsTrustStatus = "trusted" | "requires_trust" | "untrusted" | "unknown" | "not_enabled";

export interface ServerTlsTrustInfo {
  enabled: boolean;
  trustStatus: ServerTlsTrustStatus;
  requiresTrust: boolean;
  isSelfSigned: boolean;
  trusted?: boolean;
  fingerprintSha256?: string;
  subject?: string;
  issuer?: string;
  validFrom?: string;
  validTo?: string;
  warning?: string;
}

export interface PairingSecurityMetadata {
  transport?: PairingTransportMetadata;
  tls?: ServerTlsTrustInfo;
  backendTlsEnabled?: boolean;
}

export interface BaseUrlSecurity {
  kind: BaseUrlSecurityKind;
  normalizedBaseUrl: string;
  protocol: "http:" | "https:";
  webSocketProtocol: WebSocketProtocol;
  host: string;
  hostname: string;
  isHttps: boolean;
  isLoopback: boolean;
  isInsecureLan: boolean;
  backendTlsEnabled: boolean;
  requiresTlsTrust: boolean;
  requiresExplicitAllow: boolean;
  serverTls?: ServerTlsTrustInfo;
  backendSecurity?: PairingSecurityMetadata;
  warning?: string;
}

export interface WebSocketConnectionInfo {
  url: string;
  protocols: string[];
  security: BaseUrlSecurity;
  warning?: string;
}

export type BackendErrorCode =
  | "auth_expired"
  | "forbidden"
  | "gone"
  | "invalid_json"
  | "invalid_pairing_response"
  | "network"
  | "not_found"
  | "rate_limited"
  | "server_error"
  | "validation"
  | "unknown";

const MOBILE_AUTH_WS_PROTOCOL_PREFIX = "lengrvis.mobile.token.";
const REMOTE_INPUT_SCOPE = "remote:input";
const SESSION_TOKEN_EXPIRY_SKEW_MS = 1000;
const WEB_SOCKET_SUBPROTOCOL_TOKEN_PATTERN = /^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$/;
const remoteInputGrantTokens = new Map<string, RemoteInputGrantToken>();
export const INSECURE_LAN_HTTP_WARNING = "当前电脑地址使用非本机 HTTP，手机 token、远程输入授权和屏幕连接不能通过局域网明文传输。请在桌面端启用 HTTPS/WSS 或使用受信任证书后重新配对。";
export const SELF_SIGNED_TLS_WARNING = "此服务器使用自签或未受系统信任的 HTTPS 证书。请在电脑端核对证书指纹；手机系统信任前，本应用不会安装证书。";
export const BACKEND_TLS_DISABLED_WARNING = "后端当前未启用 TLS。请输入 HTTPS 地址；非本机局域网 HTTP 不能承载手机 token、屏幕或远程输入连接。";

export class AuthExpiredError extends Error {
  readonly status = 401;
  readonly code = "auth_expired";

  constructor(message = "这台手机已断开连接。请在 Lengrvis 中重新连接。") {
    super(message);
    this.name = "AuthExpiredError";
  }
}

export class ForbiddenError extends Error {
  readonly status = 403;
  readonly code = "forbidden";

  constructor(message = "这台手机没有权限执行该操作。") {
    super(message);
    this.name = "ForbiddenError";
  }
}

export class BackendHttpError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code: BackendErrorCode;

  constructor(status: number, detail: string, code: BackendErrorCode = backendErrorCodeForStatus(status)) {
    super(detail || "Lengrvis 未能完成该请求，请重试。");
    this.name = "BackendHttpError";
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

export class InsecureLanBaseUrlError extends Error {
  readonly security: BaseUrlSecurity;

  constructor(security: BaseUrlSecurity, message = security.warning || INSECURE_LAN_HTTP_WARNING) {
    super(message);
    this.name = "InsecureLanBaseUrlError";
    this.security = security;
  }
}

export async function pairWithBackend(
  baseUrl: string,
  code: string,
  deviceName: string,
): Promise<PairingSession> {
  const baseUrlSecurity = assertSafeBaseUrl(baseUrl);
  const normalizedBaseUrl = baseUrlSecurity.normalizedBaseUrl;
  const response = await fetch(`${normalizedBaseUrl}/api/pair/confirm`, {
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
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/approvals/pending`, {
    headers: authHeaders(safeSession.token),
  });
  return parseJson<BackendApproval[]>(response);
}

export async function getApprovalDetail(session: PairingSession, approvalId: string): Promise<ApprovalDetail> {
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}`, {
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
  const safeSession = assertSafePairingSession(session);
  if (decision === "approved") {
    const remoteInputGrantToken = await remoteInputGrantTokenForApproval(safeSession, approvalId, options);
    if (remoteInputGrantToken) {
      const response = await fetch(`${safeSession.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}/decision`, {
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
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}/${action}`, {
    method: "POST",
    headers: authHeaders(safeSession.token),
  });
  return parseJson<BackendApproval>(response);
}

export async function disconnectMobileDevice(session: PairingSession): Promise<void> {
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/devices/${encodeURIComponent(safeSession.deviceId)}`, {
    method: "DELETE",
    headers: authHeaders(safeSession.token),
  });
  await parseJson<unknown>(response);
}

export async function listMobileTasks(session: PairingSession): Promise<MobileTask[]> {
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/tasks`, {
    headers: authHeaders(safeSession.token),
  });
  const payload = await parseJson<{ tasks: MobileTask[] }>(response);
  return payload.tasks;
}

export async function createMobileTask(
  session: PairingSession,
  request: { template_id: MobileTaskTemplateId; user_input?: string; mode: MobileTaskMode },
): Promise<MobileTaskLaunchResult> {
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/tasks`, {
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
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/tasks/${encodeURIComponent(taskId)}/follow-up`, {
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
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/tasks/${encodeURIComponent(taskId)}/${command}`, {
    method: "POST",
    headers: authHeaders(safeSession.token),
  });
  return parseJson<MobileTask>(response);
}

export async function claimRemoteInputGrantToken(session: PairingSession, grantId: string): Promise<RemoteInputGrantToken> {
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/remote-input-grants/${encodeURIComponent(grantId)}/token`, {
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
  const safeSession = assertSafePairingSession(session);
  const response = await fetch(`${safeSession.baseUrl}/api/mobile/remote-input-grants/${encodeURIComponent(grantId)}`, {
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

async function remoteInputGrantTokenForApproval(
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

function rememberRemoteInputGrantToken(payload: RemoteInputGrantToken): void {
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

function forgetRemoteInputGrantToken(grantId: string): void {
  if (grantId) remoteInputGrantTokens.delete(grantId);
}

function usableRemoteInputGrantToken(grantId: string): RemoteInputGrantToken | null {
  const cached = remoteInputGrantTokens.get(grantId);
  if (!cached) return null;
  if (!isRemoteInputGrantTokenUsable(cached)) {
    remoteInputGrantTokens.delete(grantId);
    return null;
  }
  return cached;
}

function usableRemoteInputGrantTokenForApproval(approval: BackendApproval, session: PairingSession): RemoteInputGrantToken | null {
  const grantId = remoteInputApprovalGrantId(approval);
  if (grantId) return usableRemoteInputGrantTokenForSession(grantId, session);
  const bindingRef = approval.remote_input_binding?.binding_ref || "";
  if (!bindingRef) return null;
  const cached = usableRemoteInputGrantTokenForSession(bindingRef, session);
  if (cached && cached.grant?.binding_ref === bindingRef) return cached;
  return null;
}

function usableRemoteInputGrantTokenForSession(grantId: string, session: PairingSession): RemoteInputGrantToken | null {
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

function validateRemoteInputGrantToken(
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

function isRemoteInputGrantTokenUsable(payload: RemoteInputGrantToken, nowMs = Date.now()): boolean {
  if (!payload.token || payload.token_type !== "Bearer") return false;
  if (payload.grant && !isRemoteInputGrantUsable(payload.grant, nowMs)) return false;
  const expiresAt = Date.parse(payload.expires_at || payload.grant?.expires_at || "");
  if (!Number.isFinite(expiresAt) || expiresAt <= nowMs) return false;
  if (Number.isFinite(payload.expires_in) && payload.expires_in <= 0) return false;
  return true;
}

function isRemoteInputGrantUsable(grant: RemoteInputGrant | null | undefined, nowMs = Date.now()): grant is RemoteInputGrant {
  if (!grant) return false;
  if (normalizedRemoteInputGrantText(grant.scope) !== REMOTE_INPUT_SCOPE) return false;
  if (normalizedRemoteInputGrantText(grant.status) !== "active") return false;
  if (normalizedRemoteInputGrantText(grant.revoked_at)) return false;
  const expiresAt = Date.parse(grant.expires_at || "");
  return Number.isFinite(expiresAt) && expiresAt > nowMs;
}

function normalizedRemoteInputGrantText(value: string | undefined): string {
  return String(value ?? "").trim().toLowerCase();
}

function isRemoteInputApproval(approval: BackendApproval): boolean {
  return (
    approval.approval_type === "remote_input" ||
    approval.source === "remote_input" ||
    approval.required_mobile_scopes?.includes(REMOTE_INPUT_SCOPE) === true
  );
}

function remoteInputApprovalGrantId(approval: BackendApproval): string {
  return approval.source_grant_id ?? "";
}

function assertRemoteInputApprovalMatchesSession(
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

function assertRemoteInputApprovalRejectAllowedForSession(session: PairingSession, approval: BackendApproval): void {
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

export function approvalWebSocketConnectionInfo(session: PairingSession): WebSocketConnectionInfo {
  const safeSession = assertSafePairingSession(session);
  return webSocketConnectionInfo(safeSession, "/ws/mobile/approvals", mobileAuthWebSocketProtocols(safeSession));
}

export function remoteScreenWebSocketConnectionInfo(session: PairingSession): WebSocketConnectionInfo {
  const safeSession = assertSafePairingSession(session);
  return webSocketConnectionInfo(safeSession, "/ws/remote/screen", mobileAuthWebSocketProtocols(safeSession));
}

export function remoteInputWebSocketConnectionInfo(session: PairingSession, token: string): WebSocketConnectionInfo {
  const safeSession = assertSafePairingSession(session);
  return webSocketConnectionInfo(safeSession, "/ws/remote/input", mobileTokenWebSocketProtocols(token));
}

export function approvalWebSocketUrl(session: PairingSession): string {
  return approvalWebSocketConnectionInfo(session).url;
}

export function remoteScreenWebSocketUrl(session: PairingSession): string {
  return remoteScreenWebSocketConnectionInfo(session).url;
}

export function remoteInputWebSocketUrl(session: PairingSession): string {
  return webSocketConnectionInfo(session, "/ws/remote/input").url;
}

export function mobileAuthWebSocketProtocols(session: PairingSession): string[] {
  const safeSession = assertSafePairingSession(session);
  assertWebSocketSubprotocolToken(safeSession.token);
  return webSocketProtocolList(`${MOBILE_AUTH_WS_PROTOCOL_PREFIX}${safeSession.token}`);
}

export function mobileTokenWebSocketProtocols(token: string): string[] {
  assertWebSocketSubprotocolToken(token);
  return webSocketProtocolList(`${MOBILE_AUTH_WS_PROTOCOL_PREFIX}${token}`);
}

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error("请输入 Lengrvis 中显示的电脑地址。");
  const hasProtocol = /^[a-z][a-z\d+\-.]*:\/\//i.test(trimmed);
  if (hasProtocol && !/^https?:\/\//i.test(trimmed)) {
    throw new Error("电脑地址必须以 http:// 或 https:// 开头。");
  }
  const withProtocol = hasProtocol ? trimmed : `http://${trimmed}`;
  const parsed = new URL(withProtocol);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("电脑地址必须以 http:// 或 https:// 开头。");
  }
  return parsed.origin;
}

export function isLoopbackBaseUrl(value: string): boolean {
  try {
    return describeBaseUrlSecurity(value).isLoopback;
  } catch {
    return false;
  }
}

export function describeBaseUrlSecurity(value: string, pairingMetadata?: unknown): BaseUrlSecurity {
  const normalizedBaseUrl = normalizeBaseUrl(value);
  const parsed = new URL(normalizedBaseUrl);
  const protocol = parsed.protocol as "http:" | "https:";
  const hostname = parsed.hostname.toLowerCase();
  const isHttps = protocol === "https:";
  const isLoopback = isLoopbackHostname(hostname);
  const isInsecureLan = protocol === "http:" && !isLoopback;
  const kind: BaseUrlSecurityKind = isHttps ? "https" : isLoopback ? "loopbackHttp" : "insecureLan";
  const security: BaseUrlSecurity = {
    kind,
    normalizedBaseUrl,
    protocol,
    webSocketProtocol: isHttps ? "wss:" : "ws:",
    host: parsed.host,
    hostname,
    isHttps,
    isLoopback,
    isInsecureLan,
    backendTlsEnabled: isHttps,
    requiresTlsTrust: false,
    requiresExplicitAllow: isInsecureLan,
    warning: isInsecureLan ? INSECURE_LAN_HTTP_WARNING : undefined,
  };
  return mergeBaseUrlSecurityMetadata(security, normalizePairingSecurityMetadata(pairingMetadata, security));
}

// Insecure LAN base URLs are always rejected; there is intentionally no opt-out
// (see mobile-token-smoke.cjs which asserts `allowInsecureLan` has no effect).
export function assertSafeBaseUrl(value: string): BaseUrlSecurity {
  const security = describeBaseUrlSecurity(value);
  if (security.isInsecureLan) {
    throw new InsecureLanBaseUrlError(security);
  }
  return security;
}

export function describeSessionBaseUrlSecurity(session: Pick<PairingSession, "baseUrl" | "baseUrlSecurity" | "security">): BaseUrlSecurity {
  const pairingMetadata =
    session.security ??
    session.baseUrlSecurity?.backendSecurity ??
    (session.baseUrlSecurity?.serverTls ? { tls: session.baseUrlSecurity.serverTls } : undefined);
  return describeBaseUrlSecurity(session.baseUrl, pairingMetadata);
}

export function assertSafePairingSession(session: PairingSession): PairingSession {
  const baseUrlSecurity = describeSessionBaseUrlSecurity(session);
  if (baseUrlSecurity.isInsecureLan || sessionHasUnsafeRemoteTransport(baseUrlSecurity)) {
    throw new InsecureLanBaseUrlError(baseUrlSecurity);
  }
  assertUsablePairingToken(session);
  return {
    ...session,
    baseUrl: baseUrlSecurity.normalizedBaseUrl,
    baseUrlSecurity,
    ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
  };
}

function webSocketConnectionInfo(session: PairingSession, pathname: string, protocols: string[] = []): WebSocketConnectionInfo {
  const safeSession = assertSafePairingSession(session);
  const security = safeSession.baseUrlSecurity;
  if (sessionHasUnsafeRemoteTransport(security)) {
    throw new InsecureLanBaseUrlError(security);
  }
  const url = new URL(pathname, security.normalizedBaseUrl);
  url.protocol = security.webSocketProtocol;
  return {
    url: url.toString(),
    protocols,
    security,
    warning: security.warning,
  };
}

export function normalizePairingSecurityMetadata(value: unknown, fallback?: BaseUrlSecurity): PairingSecurityMetadata | undefined {
  const root = asRecord(value);
  if (!root) return undefined;

  const serverRoot = asRecord(root.server);
  const transportSecurityRoot =
    asRecord(root.transport_security) ??
    asRecord(root.transportSecurity) ??
    asRecord(serverRoot?.transport_security) ??
    asRecord(serverRoot?.transportSecurity);
  const securityRoot = asRecord(root.security) ?? root;
  const transportRoot = asRecord(root.transport) ?? asRecord(securityRoot.transport) ?? transportSecurityRoot ?? serverRoot;
  const tlsRoot =
    asRecord(root.tls) ??
    asRecord(securityRoot.tls) ??
    asRecord(transportSecurityRoot?.tls) ??
    asRecord(root.certificate) ??
    asRecord(securityRoot.certificate) ??
    asRecord(transportSecurityRoot?.certificate) ??
    asRecord(root.cert) ??
    asRecord(securityRoot.cert) ??
    asRecord(transportSecurityRoot?.cert) ??
    asRecord(transportRoot?.tls) ??
    transportSecurityRoot;
  const hasMetadata =
    root.security !== undefined ||
    root.transport !== undefined ||
    root.transport_security !== undefined ||
    root.transportSecurity !== undefined ||
    serverRoot?.transport_security !== undefined ||
    serverRoot?.transportSecurity !== undefined ||
    hasKnownTransportMetadata(securityRoot) ||
    hasKnownTransportMetadata(transportRoot) ||
    hasKnownTransportMetadata(serverRoot) ||
    hasKnownTransportMetadata(transportSecurityRoot) ||
    hasKnownTlsMetadata(securityRoot) ||
    hasKnownTlsMetadata(transportSecurityRoot) ||
    hasKnownTlsMetadata(tlsRoot);
  if (!hasMetadata) return undefined;

  const transport = normalizeTransportMetadata(securityRoot, transportRoot, fallback);
  const tls = normalizeTlsTrustInfo(securityRoot, tlsRoot, transport, fallback);
  const backendTlsEnabled = tls?.enabled ?? transport?.tlsEnabled;
  if (!transport && !tls && backendTlsEnabled === undefined) return undefined;
  return {
    ...(transport ? { transport } : {}),
    ...(tls ? { tls } : {}),
    ...(backendTlsEnabled !== undefined ? { backendTlsEnabled } : {}),
  };
}

export function mergeBaseUrlSecurityMetadata(
  security: BaseUrlSecurity,
  pairingMetadata?: PairingSecurityMetadata,
): BaseUrlSecurity {
  if (!pairingMetadata) return security;
  const serverTls = pairingMetadata.tls;
  const backendTlsEnabled = pairingMetadata.backendTlsEnabled ?? serverTls?.enabled ?? pairingMetadata.transport?.tlsEnabled ?? security.backendTlsEnabled;
  const transportWebSocketProtocol = pairingMetadata.transport?.webSocketScheme === "wss"
    ? "wss:"
    : pairingMetadata.transport?.webSocketScheme === "ws"
      ? "ws:"
      : security.webSocketProtocol;
  const remoteTransportUnsafe = !security.isLoopback && (!backendTlsEnabled || transportWebSocketProtocol !== "wss:");
  const requiresTlsTrust = Boolean(serverTls?.requiresTrust);
  const warning =
    security.warning ??
    (requiresTlsTrust
      ? serverTls?.warning ?? SELF_SIGNED_TLS_WARNING
      : remoteTransportUnsafe
        ? BACKEND_TLS_DISABLED_WARNING
        : undefined);
  return {
    ...security,
    kind: remoteTransportUnsafe ? "insecureLan" : security.kind,
    isInsecureLan: security.isInsecureLan || remoteTransportUnsafe,
    backendTlsEnabled,
    webSocketProtocol: transportWebSocketProtocol,
    requiresTlsTrust,
    serverTls,
    backendSecurity: pairingMetadata,
    warning,
  };
}

export function formatTlsFingerprint(fingerprint?: string): string {
  const normalized = normalizeFingerprint(fingerprint);
  if (!normalized) return "";
  if (normalized.includes(":")) return normalized;
  return normalized.match(/.{1,2}/g)?.join(":") ?? normalized;
}

function normalizePairingServerInfo(server: PairResult["server"] | undefined): PairingServerInfo | undefined {
  if (!server || !server.host || !Number.isFinite(server.port)) return undefined;
  const origin = server.origin ?? server.base_url ?? server.url;
  const transportSecurity = normalizePairingSecurityMetadata(server.transport_security);
  return {
    host: server.host,
    port: server.port,
    ...(server.protocol ?? server.scheme ? { protocol: server.protocol ?? server.scheme } : {}),
    ...(server.scheme ? { scheme: server.scheme } : {}),
    ...(server.url ? { url: server.url } : {}),
    ...(server.base_url ?? origin ? { baseUrl: server.base_url ?? origin } : {}),
    ...(origin ? { origin } : {}),
    ...(transportSecurity ? { transportSecurity } : {}),
  };
}

function normalizeTransportMetadata(
  securityRoot: Record<string, unknown>,
  transportRoot: Record<string, unknown> | undefined,
  fallback?: BaseUrlSecurity,
): PairingTransportMetadata | undefined {
  const httpScheme = normalizeHttpScheme(
    firstText(transportRoot, "http_scheme", "httpScheme", "scheme", "protocol", "transport") ??
      firstText(securityRoot, "http_scheme", "httpScheme", "scheme", "protocol", "transport"),
  );
  const webSocketScheme = normalizeWebSocketScheme(
    firstText(transportRoot, "websocket_scheme", "webSocketScheme", "ws_scheme", "wsScheme") ??
      firstText(securityRoot, "websocket_scheme", "webSocketScheme", "ws_scheme", "wsScheme"),
  );
  const explicitTlsEnabled =
    firstBoolean(transportRoot, "tls_enabled", "tlsEnabled", "https_enabled", "httpsEnabled") ??
    firstBoolean(securityRoot, "tls_enabled", "tlsEnabled", "https_enabled", "httpsEnabled", "backend_tls_enabled", "backendTlsEnabled");
  const tlsEnabled = explicitTlsEnabled ?? (httpScheme === "https" ? true : httpScheme === "http" ? false : undefined);
  const advertisedBaseUrl =
    firstText(transportRoot, "advertised_base_url", "advertisedBaseUrl", "base_url", "baseUrl", "origin") ??
    firstText(securityRoot, "advertised_base_url", "advertisedBaseUrl", "base_url", "baseUrl", "server_origin", "serverOrigin", "origin");
  const serverUrl =
    firstText(transportRoot, "server_url", "serverUrl", "url", "origin") ??
    firstText(securityRoot, "server_url", "serverUrl", "url", "server_origin", "serverOrigin", "origin");

  if (
    httpScheme === undefined &&
    webSocketScheme === undefined &&
    tlsEnabled === undefined &&
    advertisedBaseUrl === undefined &&
    serverUrl === undefined
  ) {
    return undefined;
  }

  return {
    ...(httpScheme ? { httpScheme } : fallback ? { httpScheme: fallback.isHttps ? "https" : "http" } : {}),
    ...(webSocketScheme ? { webSocketScheme } : fallback ? { webSocketScheme: fallback.isHttps ? "wss" : "ws" } : {}),
    ...(tlsEnabled !== undefined ? { tlsEnabled } : {}),
    ...(advertisedBaseUrl ? { advertisedBaseUrl } : {}),
    ...(serverUrl ? { serverUrl } : {}),
  };
}

function normalizeTlsTrustInfo(
  securityRoot: Record<string, unknown>,
  tlsRoot: Record<string, unknown> | undefined,
  transport: PairingTransportMetadata | undefined,
  fallback?: BaseUrlSecurity,
): ServerTlsTrustInfo | undefined {
  if (!hasKnownTlsMetadata(securityRoot) && !hasKnownTlsMetadata(tlsRoot) && transport?.tlsEnabled === undefined) return undefined;
  const enabled =
    firstBoolean(tlsRoot, "enabled", "tls_enabled", "tlsEnabled", "https_enabled", "httpsEnabled") ??
    firstBoolean(securityRoot, "tls_enabled", "tlsEnabled", "https_enabled", "httpsEnabled", "backend_tls_enabled", "backendTlsEnabled") ??
    transport?.tlsEnabled ??
    fallback?.isHttps ??
    false;
  const statusRaw = (
    firstText(tlsRoot, "trust_status", "trustStatus", "status", "state") ??
    firstText(securityRoot, "trust_status", "trustStatus", "status", "state") ??
    ""
  ).toLowerCase();
  const normalizedStatus = statusRaw.replace(/[\s-]+/g, "_");
  const isSelfSigned = Boolean(
    firstBoolean(tlsRoot, "self_signed", "selfSigned", "is_self_signed", "isSelfSigned") ??
      firstBoolean(securityRoot, "self_signed", "selfSigned", "is_self_signed", "isSelfSigned") ??
      (normalizedStatus.includes("self_signed") || normalizedStatus.includes("selfsigned")),
  );
  const trusted =
    firstBoolean(tlsRoot, "trusted", "is_trusted", "isTrusted", "system_trusted", "systemTrusted") ??
    firstBoolean(securityRoot, "trusted", "is_trusted", "isTrusted", "system_trusted", "systemTrusted");
  const statusRequiresTrust =
    normalizedStatus.includes("requires_trust") ||
    normalizedStatus.includes("trust_required") ||
    normalizedStatus.includes("untrusted") ||
    normalizedStatus.includes("unknown_ca") ||
    normalizedStatus.includes("self_signed") ||
    normalizedStatus.includes("selfsigned");
  const requiresTrust = Boolean(
    firstBoolean(tlsRoot, "requires_trust", "requiresTrust", "trust_required", "trustRequired") ??
      firstBoolean(securityRoot, "requires_trust", "requiresTrust", "trust_required", "trustRequired") ??
      (statusRequiresTrust || (isSelfSigned && trusted !== true)),
  );
  const trustStatus: ServerTlsTrustStatus = !enabled
    ? "not_enabled"
    : requiresTrust
      ? "requires_trust"
      : trusted === true || normalizedStatus === "trusted" || normalizedStatus === "system_trusted" || normalizedStatus === "valid"
        ? "trusted"
        : normalizedStatus === "untrusted"
          ? "untrusted"
          : "unknown";
  const fingerprintSha256 = normalizeFingerprint(
    firstText(
      tlsRoot,
      "fingerprint_sha256",
      "fingerprintSha256",
      "sha256_fingerprint",
      "sha256Fingerprint",
      "certificate_fingerprint_sha256",
      "certificateFingerprintSha256",
      "fingerprint",
    ) ??
      firstText(
        securityRoot,
        "fingerprint_sha256",
        "fingerprintSha256",
        "sha256_fingerprint",
        "sha256Fingerprint",
        "certificate_fingerprint_sha256",
        "certificateFingerprintSha256",
        "fingerprint",
      ),
  );
  return {
    enabled,
    trustStatus,
    requiresTrust,
    isSelfSigned,
    ...(trusted !== undefined ? { trusted } : {}),
    ...(fingerprintSha256 ? { fingerprintSha256 } : {}),
    ...(firstText(tlsRoot, "subject") ?? firstText(securityRoot, "subject") ? { subject: firstText(tlsRoot, "subject") ?? firstText(securityRoot, "subject") } : {}),
    ...(firstText(tlsRoot, "issuer") ?? firstText(securityRoot, "issuer") ? { issuer: firstText(tlsRoot, "issuer") ?? firstText(securityRoot, "issuer") } : {}),
    ...(firstText(tlsRoot, "valid_from", "validFrom", "not_before", "notBefore") ?? firstText(securityRoot, "valid_from", "validFrom", "not_before", "notBefore")
      ? { validFrom: firstText(tlsRoot, "valid_from", "validFrom", "not_before", "notBefore") ?? firstText(securityRoot, "valid_from", "validFrom", "not_before", "notBefore") }
      : {}),
    ...(firstText(tlsRoot, "valid_to", "validTo", "not_after", "notAfter") ?? firstText(securityRoot, "valid_to", "validTo", "not_after", "notAfter")
      ? { validTo: firstText(tlsRoot, "valid_to", "validTo", "not_after", "notAfter") ?? firstText(securityRoot, "valid_to", "validTo", "not_after", "notAfter") }
      : {}),
    ...(requiresTrust ? { warning: SELF_SIGNED_TLS_WARNING } : {}),
  };
}

function hasKnownTransportMetadata(value: Record<string, unknown> | undefined): boolean {
  return Boolean(
    value &&
      [
        "transport",
        "http_scheme",
        "httpScheme",
        "websocket_scheme",
        "webSocketScheme",
        "ws_scheme",
        "wsScheme",
        "scheme",
        "protocol",
        "tls_enabled",
        "tlsEnabled",
        "https_enabled",
        "httpsEnabled",
        "backend_tls_enabled",
        "backendTlsEnabled",
        "advertised_base_url",
        "advertisedBaseUrl",
        "server_url",
        "serverUrl",
        "server_origin",
        "serverOrigin",
        "origin",
      ].some((key) => key in value),
  );
}

function hasKnownTlsMetadata(value: Record<string, unknown> | undefined): boolean {
  return Boolean(
    value &&
      [
        "tls",
        "certificate",
        "cert",
        "enabled",
        "trust_status",
        "trustStatus",
        "requires_trust",
        "requiresTrust",
        "trust_required",
        "trustRequired",
        "self_signed",
        "selfSigned",
        "is_self_signed",
        "isSelfSigned",
        "trusted",
        "is_trusted",
        "isTrusted",
        "system_trusted",
        "systemTrusted",
        "fingerprint_sha256",
        "fingerprintSha256",
        "sha256_fingerprint",
        "sha256Fingerprint",
        "certificate_fingerprint_sha256",
        "certificateFingerprintSha256",
        "fingerprint",
      ].some((key) => key in value),
  );
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function firstText(value: Record<string, unknown> | undefined, ...keys: string[]): string | undefined {
  if (!value) return undefined;
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
    if (typeof candidate === "number" || typeof candidate === "boolean") return String(candidate);
  }
  return undefined;
}

function firstBoolean(value: Record<string, unknown> | undefined, ...keys: string[]): boolean | undefined {
  if (!value) return undefined;
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "boolean") return candidate;
    if (typeof candidate === "string") {
      const normalized = candidate.trim().toLowerCase();
      if (["true", "1", "yes", "enabled", "on"].includes(normalized)) return true;
      if (["false", "0", "no", "disabled", "off"].includes(normalized)) return false;
    }
    if (typeof candidate === "number") return candidate !== 0;
  }
  return undefined;
}

function normalizeHttpScheme(value: string | undefined): "http" | "https" | string | undefined {
  const normalized = value?.trim().toLowerCase().replace(/:$/, "");
  if (!normalized) return undefined;
  if (normalized === "http" || normalized === "https") return normalized;
  return normalized;
}

function normalizeWebSocketScheme(value: string | undefined): "ws" | "wss" | string | undefined {
  const normalized = value?.trim().toLowerCase().replace(/:$/, "");
  if (!normalized) return undefined;
  if (normalized === "ws" || normalized === "wss") return normalized;
  return normalized;
}

function normalizeFingerprint(value: string | undefined): string {
  return value?.trim().replace(/\s+/g, "").toUpperCase() ?? "";
}

function isLoopbackHostname(hostname: string): boolean {
  return hostname === "localhost" || hostname === "::1" || hostname === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(hostname);
}

function pairingSessionExpiresAt(payload: Pick<PairResult, "expires_in" | "expires_at" | "expiresAt">): string | undefined {
  const explicit = typeof payload.expires_at === "string" ? payload.expires_at : typeof payload.expiresAt === "string" ? payload.expiresAt : "";
  const explicitMs = Date.parse(explicit);
  if (Number.isFinite(explicitMs)) return new Date(explicitMs).toISOString();
  if (!Number.isFinite(payload.expires_in)) return undefined;
  if (payload.expires_in <= 0) return new Date(0).toISOString();
  return new Date(Date.now() + payload.expires_in * 1000).toISOString();
}

function sessionHasUnsafeRemoteTransport(security: BaseUrlSecurity): boolean {
  return !security.isLoopback && (!security.backendTlsEnabled || security.webSocketProtocol !== "wss:");
}

function validatePairResult(payload: unknown): { payload: PairResult; expiresAt: string } {
  const record = asRecord(payload);
  if (!record) {
    throw invalidPairingResponse("Pairing response must be a JSON object.");
  }

  const token = typeof record.token === "string" ? record.token : "";
  const tokenType = typeof record.token_type === "string" ? record.token_type : "";
  const deviceId = typeof record.device_id === "string" ? record.device_id : "";
  if (!token || tokenType !== "Bearer" || !deviceId) {
    throw invalidPairingResponse("Pairing response is missing a bearer token or device id.");
  }
  if (!isWebSocketSubprotocolToken(token)) {
    throw invalidPairingResponse("Pairing response token cannot be used as a WebSocket subprotocol.");
  }

  const expiresAt = pairingSessionExpiresAt(payload as Pick<PairResult, "expires_in" | "expires_at" | "expiresAt">);
  if (!expiresAt) {
    throw invalidPairingResponse("Pairing response is missing a usable token expiry.");
  }
  if (isExpiredTimestamp(expiresAt, Date.now() + SESSION_TOKEN_EXPIRY_SKEW_MS)) {
    throw new AuthExpiredError("Pairing token is already expired. Generate a new pairing code.");
  }
  return { payload: payload as PairResult, expiresAt };
}

function assertUsablePairingToken(session: PairingSession): void {
  if (!session.token?.trim()) {
    throw new AuthExpiredError("Mobile session token is missing. Pair this phone again.");
  }
  assertWebSocketSubprotocolToken(session.token);
  if (isExpiredTimestamp(session.expiresAt, Date.now() + SESSION_TOKEN_EXPIRY_SKEW_MS)) {
    throw new AuthExpiredError("Mobile session token has expired. Pair this phone again.");
  }
}

function isExpiredTimestamp(value: string | undefined, nowMs = Date.now()): boolean {
  if (!value) return false;
  const expiresAt = Date.parse(value);
  return !Number.isFinite(expiresAt) || expiresAt <= nowMs;
}

function assertWebSocketSubprotocolToken(token: string): void {
  if (!isWebSocketSubprotocolToken(token)) {
    throw new ForbiddenError("WebSocket tokens must be sent as valid Sec-WebSocket-Protocol tokens.");
  }
}

function isWebSocketSubprotocolToken(token: string): boolean {
  return Boolean(token && token.trim() === token && WEB_SOCKET_SUBPROTOCOL_TOKEN_PATTERN.test(token));
}

function webSocketProtocolList(protocol: string): string[] {
  return [protocol];
}

type RemoteInputGrantJsonContext = "claim" | "use";

async function parseRemoteInputGrantJson<T>(response: Response, context: RemoteInputGrantJsonContext): Promise<T> {
  const data = await response.json().catch(() => undefined);
  if (!response.ok) {
    throw remoteInputGrantResponseError(response.status, responseDetailMessage(data), context);
  }
  return data as T;
}

function remoteInputGrantResponseError(status: number, detail: string, context: RemoteInputGrantJsonContext): Error {
  const normalized = detail.toLowerCase();
  if (status === 401) {
    if (normalized.includes("mobile device")) {
      return new AuthExpiredError(detail || undefined);
    }
    if (normalized.includes("remote input grant expired")) {
      return new BackendHttpError(410, detail || "Remote input grant expired.");
    }
    if (normalized.includes("remote input grant")) {
      return new ForbiddenError(detail || undefined);
    }
    if (context === "use" && (normalized.includes("mobile token expired") || normalized.includes("invalid mobile token"))) {
      return new BackendHttpError(410, detail || "Remote input grant token expired.");
    }
    return new AuthExpiredError(detail || undefined);
  }
  if (status === 403) {
    if (normalized.includes("remote input grant")) {
      return new BackendHttpError(410, terminalRemoteInputGrantDetail(detail));
    }
    return new ForbiddenError(detail || undefined);
  }
  return new BackendHttpError(status, detail);
}

function terminalRemoteInputGrantDetail(detail: string): string {
  const normalized = detail.trim().toLowerCase();
  if (!normalized) return "Remote input grant is not active.";
  if (normalized.includes("not active") || normalized.includes("expired") || normalized.includes("revoked")) return detail;
  return "Remote input grant is not active.";
}

function backendErrorCodeForStatus(status: number): BackendErrorCode {
  if (status === 400 || status === 422) return "validation";
  if (status === 401) return "auth_expired";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 410) return "gone";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server_error";
  if (status <= 0) return "network";
  return "unknown";
}

function invalidPairingResponse(detail: string): BackendHttpError {
  return new BackendHttpError(400, detail, "invalid_pairing_response");
}

async function parseJson<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = responseDetailMessage(data);
    if (response.status === 401) {
      throw new AuthExpiredError(detail || undefined);
    }
    if (response.status === 403) {
      throw new ForbiddenError(detail || undefined);
    }
    throw new BackendHttpError(response.status, detail);
  }
  return data as T;
}

function responseDetailMessage(data: unknown): string {
  if (!data || typeof data !== "object" || !("detail" in data)) return "";
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg?: unknown }).msg) : ""))
      .filter(Boolean);
    return messages.join("；");
  }
  if (detail && typeof detail === "object" && "msg" in detail) {
    return String((detail as { msg?: unknown }).msg);
  }
  return detail === undefined || detail === null ? "" : String(detail);
}

function authHeaders(token: string): Record<string, string> {
  return {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
  };
}

function jsonAuthHeaders(token: string): Record<string, string> {
  return {
    ...authHeaders(token),
    "Content-Type": "application/json",
  };
}
