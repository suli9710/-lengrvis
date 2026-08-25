

export interface PairResult {
  token: string;
  token_type: "Bearer";
  device_id: string;
  refresh_token: string;
  refresh_expires_in: number;
  refresh_expires_at?: string;
  token_family_id: string;
  device_credential_id: string;
  device_trust?: MobileDeviceTrustMetadata;
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

export type BackendApprovalStatus = "pending" | "approved" | "rejected" | "expired" | (string & {});

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
  status: BackendApprovalStatus;
  created_at: string;
  expires_at?: string;
  decided_at?: string | null;
  authorized_at?: string | null;
  source?: string;
  source_device_id?: string;
  source_grant_id?: string;
  allowed_device_ids?: string[];
  required_mobile_scopes?: string[];
  remote_input_binding?: RemoteInputBinding;
  mobile_step_up_required?: boolean;
  mobile_step_up_satisfied?: boolean;
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
  refreshToken: string;
  deviceId: string;
  tokenFamilyId: string;
  deviceCredentialId: string;
  deviceTrust?: MobileDeviceTrustMetadata;
  expiresAt?: string;
  refreshExpiresAt?: string;
  baseUrlSecurity: BaseUrlSecurity;
  server?: PairingServerInfo;
  security?: PairingSecurityMetadata;
}

export interface MobilePushSubscription {
  provider: "expo";
  token: string;
}

export interface MobileDeviceTrustMetadata {
  attestation_verified: false;
  attestation_status: "not_verified" | "not_supported" | "unverified" | string;
  attestation_provider?: "none" | string;
  trust_basis?: "pairing_code_tls" | string;
  hardware_backed?: false;
  message?: string;
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
  device_trust?: MobileDeviceTrustMetadata;
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

export const TLS_PIN_RECORD_SCHEMA = "tls-pin-record-v1" as const;

export type TlsPinStatus = "active" | "next" | "revoked";

export interface TlsPinRecord {
  schema_version: typeof TLS_PIN_RECORD_SCHEMA;
  pin_id: string;
  origin: string;
  host: string;
  fingerprint_sha256: string;
  status: TlsPinStatus;
  created_at: string;
  expires_at: string;
  source_device_id?: string;
  revoked_at?: string;
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

export interface BackendWakeup {
  id: string;
  source: string;
  source_id: string;
  title: string;
  body: string;
  goal: string;
  mode: string;
  status: "pending" | "approved" | "rejected" | "completed" | "failed";
  run_id?: string;
  error?: string;
  due_at: string;
  decided_at?: string;
  created_at: string;
  updated_at: string;
}

// Shared protocol constants and user-facing transport warnings. They live in
// this leaf module so http/endpoints/security can all use them without
// creating import cycles.
export const MOBILE_AUTH_WS_PROTOCOL_PREFIX = "lengrvis.mobile.token.";
export const REMOTE_INPUT_SCOPE = "remote:input";
export const SESSION_TOKEN_EXPIRY_SKEW_MS = 1000;
export const WEB_SOCKET_SUBPROTOCOL_TOKEN_PATTERN = /^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$/;
export const INSECURE_LAN_HTTP_WARNING = "当前电脑地址使用非本机 HTTP，手机 token、远程输入授权和屏幕连接不能通过局域网明文传输。请在桌面端启用 HTTPS/WSS 或使用受信任证书后重新配对。";
export const SELF_SIGNED_TLS_WARNING = "此服务器使用自签或未受系统信任的 HTTPS 证书。请在电脑端核对证书指纹；手机系统信任前，本应用不会安装证书。";
export const BACKEND_TLS_DISABLED_WARNING = "后端当前未启用 TLS。请输入 HTTPS 地址；非本机局域网 HTTP 不能承载手机 token、屏幕或远程输入连接。";
