export const IPC_CHANNELS = {
  apiRequest: "lengrvis:api:request",
  backendStatus: "lengrvis:backend:status",
  backendStart: "lengrvis:backend:start",
  backendStop: "lengrvis:backend:stop",
  backendForeground: "lengrvis:backend:foreground",
  backendBackground: "lengrvis:backend:background",
  openExternal: "lengrvis:shell:open-external",
  getFileIcon: "lengrvis:shell:get-file-icon",
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
  openTaskFromNotification: "lengrvis:notification:open-task"
} as const;

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];

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

export const API_REQUEST_ALLOWED_KEYS = ["endpoint", "method", "query", "body", "timeoutMs"] as const;

export const API_REQUEST_DENIED_PATH_PREFIXES = [
  "/api/dev",
  "/api/index",
  "/api/mobile",
  "/api/runtime",
  "/api/ui-automation",
  "/api/ws"
] as const;

export const API_REQUEST_DENIED_EXACT_PATHS = [
  "/api/apps/launch",
  "/api/apps/open-file",
  "/api/apps/open-folder",
  "/api/pair",
  "/api/pair/code",
  "/api/pair/confirm",
  "/api/perception/capture"
] as const;
