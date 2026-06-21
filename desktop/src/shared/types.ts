import type { AcceptConsentRequest, ConsentRecord, ConsentStatusResult, LegalDocId } from "./consent";

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

export interface BackendState {}

export interface BackendStatus {}

export interface LengrvisDesktopBridge {
  api: {
    request: <TResponse = unknown, TBody = unknown>(
      request: ApiRequest<TBody>
    ) => Promise<ApiResponse<TResponse>>;
    abortInflight: (abortGroup: string) => Promise<void>;
  };
  realtime: {
    subscribe: () => () => void;
  };
  backendBaseUrl?: string;
  backend: {
    getStatus: () => Promise<unknown>;
    start: () => Promise<unknown>;
    stop: () => Promise<unknown>;
    foreground: () => Promise<unknown>;
    background: () => Promise<unknown>;
  };
  commands: {
    execute: (request: { name: string; args?: Record<string, unknown> }) => Promise<ApiResponse<unknown>>;
  };
  tasks: {
    rollback: (taskId: string) => Promise<ApiResponse<unknown>>;
  };
  cleanup: {
    execute: (body: Record<string, unknown>) => Promise<ApiResponse<unknown>>;
    rollback: (body: Record<string, unknown>) => Promise<ApiResponse<unknown>>;
  };
  skills: {
    importPackage: (path: string) => Promise<ApiResponse<unknown>>;
    refresh: () => Promise<ApiResponse<unknown>>;
  };
  localModel: {
    install: (request: { model?: string }) => Promise<ApiResponse<unknown>>;
  };
  ollama: {
    install: () => Promise<ApiResponse<unknown>>;
    pull: (request?: { model?: string }) => Promise<ApiResponse<unknown>>;
    start: () => Promise<ApiResponse<unknown>>;
  };
  runs: {
    start: (request: DesktopRunStartRequest) => Promise<ApiResponse<unknown>>;
  };
  system: {
    exportDiagnosticsPackage: () => Promise<ApiResponse<unknown>>;
  };
  documents: {
    parse: (request: DocumentParseRequest) => Promise<ApiResponse<unknown>>;
    ask: (request: DocumentAskRequest) => Promise<ApiResponse<unknown>>;
    compare: (request: DocumentCompareRequest) => Promise<ApiResponse<unknown>>;
  };
  settings: {
    confirmSensitiveChange: (patch: DesktopSettingsPatch) => Promise<ApiResponse<DesktopSensitiveChangeConfirmation>>;
    save: (patch: DesktopSettingsPatch) => Promise<ApiResponse<unknown>>;
  };
  permissionPolicy: {
    confirmRelaxation: (
      request: DesktopPermissionPolicyRelaxationRequest
    ) => Promise<ApiResponse<DesktopSensitiveChangeConfirmation>>;
    upsertRule: (request: DesktopPermissionRuleUpsertRequest) => Promise<ApiResponse<unknown>>;
    deleteRule: (request: DesktopPermissionRuleDeleteRequest) => Promise<ApiResponse<unknown>>;
  };
  mobilePairing: {
    createCode: () => Promise<ApiResponse<unknown>>;
    listDevices: () => Promise<ApiResponse<unknown>>;
    revokeDevice: (deviceId: string) => Promise<ApiResponse<unknown>>;
    createRemoteInputGrant: (request: MobilePairingRemoteInputGrantRequest) => Promise<ApiResponse<unknown>>;
    revokeRemoteInputGrant: (request: MobilePairingRevokeRemoteInputGrantRequest) => Promise<ApiResponse<unknown>>;
  };
  consent: {
    getStatus: () => Promise<ConsentStatusResult>;
    accept: (request: AcceptConsentRequest) => Promise<ConsentRecord>;
    readDoc: (docId: LegalDocId) => Promise<{ content: string; docId: LegalDocId }>;
  };
  dialog: {
    chooseDirectory: () => Promise<string | null>;
    chooseDocument: () => Promise<string | null>;
    knownFolders: () => Promise<Record<string, string | null>>;
    chooseSkillDirectory: () => Promise<string | null>;
    chooseSkillZip: () => Promise<string | null>;
  };
  browserHost: {
    getSnapshot: () => Promise<unknown>;
    open: (request: unknown) => Promise<unknown>;
    show: (sessionId: string) => Promise<unknown>;
    hide: () => Promise<unknown>;
    setBounds: (bounds: unknown) => Promise<unknown>;
    pause: (sessionId: string) => Promise<unknown>;
    resume: (sessionId: string) => Promise<unknown>;
    takeover: (sessionId: string) => Promise<unknown>;
    release: (sessionId: string) => Promise<unknown>;
    stop: (sessionId: string) => Promise<unknown>;
    performAction: (request: unknown) => Promise<unknown>;
    onSnapshot: (handler: (snapshot: unknown) => void) => () => void;
  };
  shell: {
    openExternal: (url: string) => Promise<void>;
    getFileIcon: (path: string) => Promise<string | null>;
    showItemInFolder: (path: string) => Promise<unknown>;
  };
  notifications: {
    show: (payload: NotificationPayload) => Promise<{ shown: boolean; reason?: string }>;
    onOpenTask: (handler: (taskId: string) => void) => () => void;
  };
  platform: string;
  versions: {
    app: string;
    electron: string;
    chrome: string;
    node: string;
  };
}