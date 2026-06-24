export const IPC_CHANNELS = {
  apiRequest: "lengrvis:api:request",
  apiAbortInflight: "lengrvis:api:abort-inflight",
  backendStatus: "lengrvis:backend:status",
  backendStart: "lengrvis:backend:start",
  backendStop: "lengrvis:backend:stop",
  backendForeground: "lengrvis:backend:foreground",
  backendBackground: "lengrvis:backend:background",
  commandsExecute: "lengrvis:commands:execute",
  taskRollback: "lengrvis:tasks:rollback",
  cleanupExecute: "lengrvis:cleanup:execute",
  cleanupRollback: "lengrvis:cleanup:rollback",
  skillsImport: "lengrvis:skills:import",
  skillsRefresh: "lengrvis:skills:refresh",
  localModelInstall: "lengrvis:local-model:install",
  ollamaInstall: "lengrvis:ollama:install",
  ollamaPull: "lengrvis:ollama:pull",
  ollamaStart: "lengrvis:ollama:start",
  runsStart: "lengrvis:runs:start",
  systemDiagnosticsExport: "lengrvis:system:diagnostics-export",
  documentsParse: "lengrvis:documents:parse",
  documentsAsk: "lengrvis:documents:ask",
  documentsCompare: "lengrvis:documents:compare",
  settingsConfirmSensitiveChange: "lengrvis:settings:confirm-sensitive-change",
  settingsSave: "lengrvis:settings:save",
  permissionPolicyConfirmRelaxation: "lengrvis:settings:permission-policy:confirm-relaxation",
  permissionPolicyUpsertRule: "lengrvis:settings:permission-policy:upsert-rule",
  permissionPolicyDeleteRule: "lengrvis:settings:permission-policy:delete-rule",
  mobilePairingCreateCode: "lengrvis:mobile-pairing:create-code",
  mobilePairingListDevices: "lengrvis:mobile-pairing:list-devices",
  mobilePairingRevokeDevice: "lengrvis:mobile-pairing:revoke-device",
  mobilePairingCreateRemoteInputGrant: "lengrvis:mobile-pairing:create-remote-input-grant",
  mobilePairingRevokeRemoteInputGrant: "lengrvis:mobile-pairing:revoke-remote-input-grant",
  openExternal: "lengrvis:shell:open-external",
  getFileIcon: "lengrvis:shell:get-file-icon",
  showItemInFolder: "lengrvis:shell:show-item-in-folder",
  chooseDirectory: "lengrvis:dialog:choose-directory",
  chooseDocument: "lengrvis:dialog:choose-document",
  knownFolders: "lengrvis:dialog:known-folders",
  chooseSkillDirectory: "lengrvis:dialog:choose-skill-directory",
  chooseSkillZip: "lengrvis:dialog:choose-skill-zip",
  browserHostSnapshot: "lengrvis:browser-host:snapshot",
  browserHostSnapshotChanged: "lengrvis:browser-host:snapshot-changed",
  browserHostOpen: "lengrvis:browser-host:open",
  browserHostShow: "lengrvis:browser-host:show",
  browserHostHide: "lengrvis:browser-host:hide",
  browserHostSetBounds: "lengrvis:browser-host:set-bounds",
  browserHostPause: "lengrvis:browser-host:pause",
  browserHostResume: "lengrvis:browser-host:resume",
  browserHostTakeover: "lengrvis:browser-host:takeover",
  browserHostRelease: "lengrvis:browser-host:release",
  browserHostStop: "lengrvis:browser-host:stop",
  browserHostAction: "lengrvis:browser-host:action",
  desktopWebSocketOpen: "lengrvis:desktop-ws:open",
  desktopWebSocketClose: "lengrvis:desktop-ws:close",
  desktopWebSocketEvent: "lengrvis:desktop-ws:event",
  showNotification: "lengrvis:show-notification",
  openTaskFromNotification: "lengrvis:notification:open-task",
  consentStatus: "lengrvis:consent:status",
  consentAccept: "lengrvis:consent:accept",
  consentReadDoc: "lengrvis:consent:read-doc"
} as const;

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];

export type IpcChannelSecurityPolicy = {
  schema: "none" | "string" | "object" | "apiRequest" | "typedRequest" | "dialogResult" | "event";
  capability:
    | "trusted-renderer"
    | "backend-token"
    | "native-confirmation"
    | "file-picker-grant"
    | "safe-external-url"
    | "permission-nonce"
    | "mobile-device-grant"
    | "browser-host-approval"
    | "desktop-ws-session";
  risk: "read" | "write" | "sensitive" | "external-navigation" | "event";
};

export const IPC_CHANNEL_SECURITY_POLICIES = {
  apiRequest: { schema: "apiRequest", capability: "backend-token", risk: "write" },
  apiAbortInflight: { schema: "string", capability: "trusted-renderer", risk: "write" },
  backendStatus: { schema: "none", capability: "trusted-renderer", risk: "read" },
  backendStart: { schema: "none", capability: "native-confirmation", risk: "sensitive" },
  backendStop: { schema: "none", capability: "native-confirmation", risk: "sensitive" },
  backendForeground: { schema: "none", capability: "trusted-renderer", risk: "write" },
  backendBackground: { schema: "none", capability: "trusted-renderer", risk: "write" },
  commandsExecute: { schema: "object", capability: "native-confirmation", risk: "sensitive" },
  taskRollback: { schema: "string", capability: "native-confirmation", risk: "sensitive" },
  cleanupExecute: { schema: "object", capability: "native-confirmation", risk: "sensitive" },
  cleanupRollback: { schema: "object", capability: "native-confirmation", risk: "sensitive" },
  skillsImport: { schema: "string", capability: "native-confirmation", risk: "sensitive" },
  skillsRefresh: { schema: "none", capability: "backend-token", risk: "write" },
  localModelInstall: { schema: "object", capability: "native-confirmation", risk: "sensitive" },
  ollamaInstall: { schema: "none", capability: "native-confirmation", risk: "sensitive" },
  ollamaPull: { schema: "object", capability: "native-confirmation", risk: "sensitive" },
  ollamaStart: { schema: "none", capability: "native-confirmation", risk: "sensitive" },
  runsStart: { schema: "typedRequest", capability: "backend-token", risk: "write" },
  systemDiagnosticsExport: { schema: "none", capability: "native-confirmation", risk: "sensitive" },
  documentsParse: { schema: "typedRequest", capability: "file-picker-grant", risk: "sensitive" },
  documentsAsk: { schema: "typedRequest", capability: "file-picker-grant", risk: "sensitive" },
  documentsCompare: { schema: "typedRequest", capability: "file-picker-grant", risk: "sensitive" },
  settingsConfirmSensitiveChange: { schema: "object", capability: "native-confirmation", risk: "sensitive" },
  settingsSave: { schema: "object", capability: "permission-nonce", risk: "sensitive" },
  permissionPolicyConfirmRelaxation: { schema: "typedRequest", capability: "native-confirmation", risk: "sensitive" },
  permissionPolicyUpsertRule: { schema: "typedRequest", capability: "permission-nonce", risk: "sensitive" },
  permissionPolicyDeleteRule: { schema: "typedRequest", capability: "permission-nonce", risk: "sensitive" },
  mobilePairingCreateCode: { schema: "none", capability: "native-confirmation", risk: "sensitive" },
  mobilePairingListDevices: { schema: "none", capability: "backend-token", risk: "read" },
  mobilePairingRevokeDevice: { schema: "string", capability: "mobile-device-grant", risk: "sensitive" },
  mobilePairingCreateRemoteInputGrant: { schema: "typedRequest", capability: "native-confirmation", risk: "sensitive" },
  mobilePairingRevokeRemoteInputGrant: { schema: "typedRequest", capability: "mobile-device-grant", risk: "sensitive" },
  openExternal: { schema: "string", capability: "safe-external-url", risk: "external-navigation" },
  getFileIcon: { schema: "string", capability: "file-picker-grant", risk: "read" },
  showItemInFolder: { schema: "string", capability: "file-picker-grant", risk: "write" },
  chooseDirectory: { schema: "dialogResult", capability: "trusted-renderer", risk: "read" },
  chooseDocument: { schema: "dialogResult", capability: "file-picker-grant", risk: "read" },
  knownFolders: { schema: "none", capability: "trusted-renderer", risk: "read" },
  chooseSkillDirectory: { schema: "dialogResult", capability: "trusted-renderer", risk: "read" },
  chooseSkillZip: { schema: "dialogResult", capability: "trusted-renderer", risk: "read" },
  browserHostSnapshot: { schema: "none", capability: "trusted-renderer", risk: "read" },
  browserHostSnapshotChanged: { schema: "event", capability: "trusted-renderer", risk: "event" },
  browserHostOpen: { schema: "typedRequest", capability: "native-confirmation", risk: "sensitive" },
  browserHostShow: { schema: "string", capability: "trusted-renderer", risk: "write" },
  browserHostHide: { schema: "none", capability: "trusted-renderer", risk: "write" },
  browserHostSetBounds: { schema: "typedRequest", capability: "trusted-renderer", risk: "write" },
  browserHostPause: { schema: "string", capability: "trusted-renderer", risk: "write" },
  browserHostResume: { schema: "string", capability: "trusted-renderer", risk: "write" },
  browserHostTakeover: { schema: "string", capability: "browser-host-approval", risk: "sensitive" },
  browserHostRelease: { schema: "string", capability: "browser-host-approval", risk: "sensitive" },
  browserHostStop: { schema: "string", capability: "trusted-renderer", risk: "write" },
  browserHostAction: { schema: "typedRequest", capability: "browser-host-approval", risk: "sensitive" },
  desktopWebSocketOpen: { schema: "typedRequest", capability: "desktop-ws-session", risk: "sensitive" },
  desktopWebSocketClose: { schema: "string", capability: "desktop-ws-session", risk: "write" },
  desktopWebSocketEvent: { schema: "event", capability: "desktop-ws-session", risk: "event" },
  showNotification: { schema: "typedRequest", capability: "trusted-renderer", risk: "write" },
  openTaskFromNotification: { schema: "event", capability: "trusted-renderer", risk: "event" },
  consentStatus: { schema: "none", capability: "trusted-renderer", risk: "read" },
  consentAccept: { schema: "object", capability: "trusted-renderer", risk: "write" },
  consentReadDoc: { schema: "string", capability: "trusted-renderer", risk: "read" }
} as const satisfies Record<keyof typeof IPC_CHANNELS, IpcChannelSecurityPolicy>;

export const API_REQUEST_SECURITY_LIMITS = {
  maxEndpointChars: 2048,
  maxQueryParams: 40,
  maxQueryKeyChars: 96,
  maxQueryValueChars: 2048,
  maxQueryBytes: 8192,
  maxBodyBytes: 524_288,
  maxBodyDepth: 12,
  maxBodyArrayItems: 2000,
  maxBodyObjectKeys: 200,
  maxBodyStringBytes: 524_288,
  maxTimeoutMs: 120_000
} as const;

export const API_REQUEST_ALLOWED_KEYS = ["endpoint", "method", "query", "body", "timeoutMs", "abortGroup"] as const;

export const API_REQUEST_DENIED_PATH_PREFIXES = [
  "/api/dev",
  "/api/documents",
  "/api/index",
  "/api/mobile",
  "/api/pair",
  "/api/runtime",
  "/api/ui-automation",
  "/api/ws"
] as const;

export const API_REQUEST_DENIED_EXACT_PATHS = [
  "/api/apps/launch",
  "/api/apps/open-file",
  "/api/apps/open-folder",
  "/api/browser/act",
  "/api/browser/cua",
  "/api/browser/cua-run",
  "/api/browser/open-url",
  "/api/browser/screenshot",
  "/api/browser/session/close",
  "/api/browser/session/start",
  "/api/commands/execute",
  "/api/files/cleanup/execute",
  "/api/files/cleanup/rollback",
  "/api/perception/capture",
  "/api/system/diagnostics/export",
  "/api/settings/install-local-model",
  "/api/settings/ollama/install",
  "/api/settings/ollama/pull",
  "/api/settings/ollama/start",
  "/api/skills/import",
  "/api/skills/refresh"
] as const;

export const API_REQUEST_DENIED_METHOD_PATHS = [
  { method: "POST", pathPrefix: "/api/runs" },
  { method: "POST", pathPrefix: "/api/tasks/", pathSuffix: "/rollback" },
  { method: "POST", path: "/api/settings" },
  { method: "POST", path: "/api/settings/confirm-sensitive-change" },
  { method: "PUT", path: "/api/settings/permission-policy" },
  { method: "POST", pathPrefix: "/api/settings/permission-policy/rules" },
  { method: "DELETE", pathPrefix: "/api/settings/permission-policy/rules" },
  { method: "POST", path: "/api/settings/permission-policy/confirm-relaxation" }
] as const;
