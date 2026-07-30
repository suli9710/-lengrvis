export type ApiMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export type ApiQueryValue = string | number | boolean | null | undefined;

export interface ApiRequest<TBody = unknown> {
  endpoint: string;
  method?: ApiMethod;
  query?: Record<string, ApiQueryValue>;
  body?: TBody;
  headers?: never;
  timeoutMs?: number;
  /** Groups in-flight IPC fetches so a new batch can abort the previous one. */
  abortGroup?: string;
}

export interface ApiError {
  code?: string;
  message: string;
  details?: unknown;
}

export interface ApiResponse<TData = unknown> {
  ok: boolean;
  status: number;
  data?: TData;
  error?: ApiError;
  receivedAt: string;
}

export type DesktopRunMode = "privacy" | "efficiency" | "hybrid";
export type DesktopRunEngine = "auto" | "os" | "developer";

export interface DesktopRunStartRequest {
  message: string;
  mode?: DesktopRunMode;
  engine?: DesktopRunEngine;
}

export interface DesktopPerceptionSuggestionLaunchRequest {
  suggestionId: string;
  mode?: DesktopRunMode;
}

export interface DesktopHardwareAccelerationSmokeRequest {
  operation?: "warmup" | "test_generate" | "test_embedding" | "test_ocr" | "test_image_embedding";
  prompt?: string;
  maxTokens?: number;
  texts?: string[];
  modelPath?: string;
  imagePath?: string;
}

export interface DesktopCommerceLicenseInstallRequest {
  token: string;
}

export interface DesktopCommerceLicenseActivateRequest {
  activationKey: string;
  appVersion?: string;
}

export interface DesktopCommercePolicyImportRequest {
  policy: {
    rules?: DesktopPermissionRule[];
  };
  confirmationNonce?: string;
}

export interface DesktopBrowserSessionRequest {
  sessionId: string;
}

export interface DesktopMemorySaveRequest {
  content: string;
  tags?: string[];
  taskId?: string;
  kind?: string;
}

export interface DesktopMemoryRecallRequest {
  query: string;
  k?: number;
  tags?: string[];
}

export type DesktopMemoryConflictStatus = "none" | "conflicting" | "resolved" | "superseded";

export interface DesktopMemoryReviewRequest {
  memoryId: string;
  reviewedBy?: string;
  conflictStatus?: DesktopMemoryConflictStatus;
  /** Convenience flag used by the renderer when resolving a conflict. */
  resolveConflict?: boolean;
}

export interface DesktopScheduleCreateRequest {
  cron: string;
  goal: string;
  mode: DesktopRunMode;
  note?: string;
}

export interface DesktopScheduleEnableRequest {
  scheduleId: string;
  enabled: boolean;
}

export interface DesktopOpenSettingsRequest {
  uri: string;
}

export interface DesktopPrivacyEraseRequest {
  confirmationText: string;
  includeSettings: boolean;
}

export interface DesktopPrivacyEraseResponse {
  ok: boolean;
  scope: "local_only";
  deleted: {
    rows_by_table: Record<string, number>;
    rows_total: number;
    diagnostic_packages: number;
  };
  preserved: string[];
  manual_cleanup: {
    log_dirs: string;
  };
  audit: string;
}

export type DesktopSettingsPatch = Record<string, unknown>;

export interface DesktopSensitiveChangeConfirmation {
  required?: boolean;
  nonce?: string;
  expires_at?: string;
  changes?: Array<Record<string, unknown>>;
}

export interface DesktopPermissionTimeWindow {
  days?: Array<number | string>;
  start?: string;
  end?: string;
  timezone?: string;
}

export interface DesktopPermissionRule {
  id?: string;
  name?: string;
  effect?: "allow" | "deny";
  tool?: string;
  tools?: string[];
  path_pattern?: string;
  path_patterns?: string[];
  time_window?: DesktopPermissionTimeWindow | null;
  time_windows?: DesktopPermissionTimeWindow[];
  enabled?: boolean;
  reason?: string;
}

export type DesktopPermissionPolicyRelaxationRequest =
  | { action: "upsert_rule"; rule: DesktopPermissionRule }
  | { action: "delete_rule"; ruleId: string }
  | { action: "replace_policy"; policy: { rules?: DesktopPermissionRule[] } };

export interface DesktopPermissionRuleUpsertRequest {
  rule: DesktopPermissionRule;
  confirmationNonce?: string;
}

export interface DesktopPermissionRuleDeleteRequest {
  ruleId: string;
  confirmationNonce?: string;
}

export interface DesktopWebSocketSubscribeRequest {
  endpoint: string;
  query?: Record<string, ApiQueryValue>;
}

export interface DesktopWebSocketOpenRequest extends DesktopWebSocketSubscribeRequest {
  id: string;
}

export interface DesktopWebSocketOpenResult {
  ok: boolean;
  id: string;
  error?: string;
}

export type DesktopWebSocketBridgeEvent =
  | { id: string; type: "open" }
  | { id: string; type: "message"; data: string }
  | { id: string; type: "error"; message?: string }
  | { id: string; type: "close"; code?: number; reason?: string; wasClean?: boolean };

export interface DesktopWebSocketSubscribeHandlers {
  onOpen?: () => void;
  onMessage?: (data: string) => void;
  onError?: (error: { message?: string }) => void;
  onClose?: (event: { code?: number; reason?: string; wasClean?: boolean }) => void;
}

export interface MobilePairingRemoteInputGrantRequest {
  deviceId: string;
  expiresInSeconds?: number;
}

export interface MobilePairingRevokeRemoteInputGrantRequest {
  deviceId: string;
  grantId: string;
}

export interface NotificationPayload {
  title: string;
  body: string;
  taskId?: string;
  severity: "info" | "warning" | "error";
}

export type BackendState = "not_configured" | "starting" | "running" | "stopped" | "error";

export interface BackendStatus {
  state: BackendState;
  baseUrl: string;
  pid?: number;
  message?: string;
  lastCheckedAt: string;
  shellMode?: "foreground" | "background";
  guardianState?: "running" | "starting" | "stopped" | "error" | string;
  fullBackendState?: "running" | "starting" | "stopped" | "error" | string;
  fullBackendPort?: number;
  lastWakeReason?: string;
  runtimeModeError?: string;
  health?: {
    ok: boolean;
    identityVerified?: boolean;
    latencyMs?: number;
  };
}
