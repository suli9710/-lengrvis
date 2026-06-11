import { contextBridge, ipcRenderer } from "electron";

import { API_REQUEST_ALLOWED_KEYS, API_REQUEST_SECURITY_LIMITS, IPC_CHANNELS } from "../shared/ipc";
import type {
  ApiMethod,
  ApiQueryValue,
  ApiRequest,
  ApiResponse,
  BrowserHostActionRequest,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  DesktopWebSocketBridgeEvent,
  DesktopWebSocketOpenResult,
  DesktopWebSocketSubscribeHandlers,
  DesktopWebSocketSubscribeRequest,
  DocumentAskRequest,
  DocumentCompareRequest,
  DocumentParseRequest,
  LengrvisDesktopBridge,
  DesktopPermissionPolicyRelaxationRequest,
  DesktopPermissionRuleDeleteRequest,
  DesktopPermissionRuleUpsertRequest,
  DesktopRunStartRequest,
  DesktopSettingsPatch,
  MobilePairingRemoteInputGrantRequest,
  MobilePairingRevokeRemoteInputGrantRequest,
  NotificationPayload
} from "../shared/types";

const preloadProcess = typeof process === "undefined" ? null : process;
const env = preloadProcess?.env ?? {};
const version = (name: keyof NodeJS.ProcessVersions): string => preloadProcess?.versions?.[name] ?? "";
let desktopWebSocketSequence = 0;
const apiRequestAllowedKeys = new Set<string>(API_REQUEST_ALLOWED_KEYS);
const apiRequestAllowedMethods = new Set<ApiMethod>(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const apiRequestReservedKeys = new Set(["__proto__", "constructor", "prototype"]);

function envValue(name: string, fallback = ""): string {
  const value = env[name];
  if (value) return value;
  return fallback;
}

const bridge: LengrvisDesktopBridge = {
  api: {
    request: <TResponse = unknown, TBody = unknown>(
      request: ApiRequest<TBody>
    ): Promise<ApiResponse<TResponse>> => {
      try {
        return ipcRenderer.invoke(IPC_CHANNELS.apiRequest, sanitizeApiBridgeRequest(request));
      } catch (error) {
        return Promise.reject(error);
      }
    },
    abortInflight: (abortGroup: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.apiAbortInflight, sanitizeApiAbortGroup(abortGroup))
  },
  realtime: {
    subscribe: subscribeDesktopWebSocket
  },
  backend: {
    getStatus: () => ipcRenderer.invoke(IPC_CHANNELS.backendStatus),
    start: () => ipcRenderer.invoke(IPC_CHANNELS.backendStart),
    stop: () => ipcRenderer.invoke(IPC_CHANNELS.backendStop),
    foreground: () => ipcRenderer.invoke(IPC_CHANNELS.backendForeground),
    background: () => ipcRenderer.invoke(IPC_CHANNELS.backendBackground)
  },
  commands: {
    execute: (request) => ipcRenderer.invoke(IPC_CHANNELS.commandsExecute, request)
  },
  tasks: {
    rollback: (taskId: string) => ipcRenderer.invoke(IPC_CHANNELS.taskRollback, taskId)
  },
  cleanup: {
    execute: (body) => ipcRenderer.invoke(IPC_CHANNELS.cleanupExecute, body),
    rollback: (body) => ipcRenderer.invoke(IPC_CHANNELS.cleanupRollback, body)
  },
  skills: {
    importPackage: (path: string) => ipcRenderer.invoke(IPC_CHANNELS.skillsImport, path),
    refresh: () => ipcRenderer.invoke(IPC_CHANNELS.skillsRefresh)
  },
  localModel: {
    install: (request) => ipcRenderer.invoke(IPC_CHANNELS.localModelInstall, request)
  },
  ollama: {
    install: () => ipcRenderer.invoke(IPC_CHANNELS.ollamaInstall),
    pull: (request) => ipcRenderer.invoke(IPC_CHANNELS.ollamaPull, request ?? {}),
    start: () => ipcRenderer.invoke(IPC_CHANNELS.ollamaStart)
  },
  runs: {
    start: (request: DesktopRunStartRequest) => ipcRenderer.invoke(IPC_CHANNELS.runsStart, request)
  },
  system: {
    exportDiagnosticsPackage: () => ipcRenderer.invoke(IPC_CHANNELS.systemDiagnosticsExport)
  },
  documents: {
    parse: (request: DocumentParseRequest) => ipcRenderer.invoke(IPC_CHANNELS.documentsParse, request),
    ask: (request: DocumentAskRequest) => ipcRenderer.invoke(IPC_CHANNELS.documentsAsk, request),
    compare: (request: DocumentCompareRequest) => ipcRenderer.invoke(IPC_CHANNELS.documentsCompare, request)
  },
  settings: {
    confirmSensitiveChange: (patch: DesktopSettingsPatch) =>
      ipcRenderer.invoke(IPC_CHANNELS.settingsConfirmSensitiveChange, patch),
    save: (patch: DesktopSettingsPatch) => ipcRenderer.invoke(IPC_CHANNELS.settingsSave, patch)
  },
  permissionPolicy: {
    confirmRelaxation: (request: DesktopPermissionPolicyRelaxationRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.permissionPolicyConfirmRelaxation, request),
    upsertRule: (request: DesktopPermissionRuleUpsertRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.permissionPolicyUpsertRule, request),
    deleteRule: (request: DesktopPermissionRuleDeleteRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.permissionPolicyDeleteRule, request)
  },
  mobilePairing: {
    createCode: () => ipcRenderer.invoke(IPC_CHANNELS.mobilePairingCreateCode),
    listDevices: () => ipcRenderer.invoke(IPC_CHANNELS.mobilePairingListDevices),
    revokeDevice: (deviceId: string) => ipcRenderer.invoke(IPC_CHANNELS.mobilePairingRevokeDevice, deviceId),
    createRemoteInputGrant: (request: MobilePairingRemoteInputGrantRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.mobilePairingCreateRemoteInputGrant, request),
    revokeRemoteInputGrant: (request: MobilePairingRevokeRemoteInputGrantRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.mobilePairingRevokeRemoteInputGrant, request)
  },
  backendBaseUrl: envValue("LENGRVIS_BACKEND_URL", "http://127.0.0.1:8000"),
  dialog: {
    chooseDirectory: () => ipcRenderer.invoke(IPC_CHANNELS.chooseDirectory),
    chooseDocument: () => ipcRenderer.invoke(IPC_CHANNELS.chooseDocument),
    knownFolders: () => ipcRenderer.invoke(IPC_CHANNELS.knownFolders),
    chooseSkillDirectory: () => ipcRenderer.invoke(IPC_CHANNELS.chooseSkillDirectory),
    chooseSkillZip: () => ipcRenderer.invoke(IPC_CHANNELS.chooseSkillZip)
  },
  browserHost: {
    getSnapshot: () => ipcRenderer.invoke(IPC_CHANNELS.browserHostSnapshot),
    open: (request: BrowserHostOpenRequest) => ipcRenderer.invoke(IPC_CHANNELS.browserHostOpen, request),
    show: (sessionId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserHostShow, sessionId),
    hide: () => ipcRenderer.invoke(IPC_CHANNELS.browserHostHide),
    setBounds: (bounds: BrowserHostBounds) => ipcRenderer.invoke(IPC_CHANNELS.browserHostSetBounds, bounds),
    pause: (sessionId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserHostPause, sessionId),
    resume: (sessionId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserHostResume, sessionId),
    takeover: (sessionId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserHostTakeover, sessionId),
    release: (sessionId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserHostRelease, sessionId),
    stop: (sessionId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserHostStop, sessionId),
    performAction: (request: BrowserHostActionRequest) => ipcRenderer.invoke(IPC_CHANNELS.browserHostAction, request),
    onSnapshot: (handler: (snapshot: BrowserHostSnapshot) => void): (() => void) => {
      const listener = (_event: Electron.IpcRendererEvent, snapshot: BrowserHostSnapshot) => {
        handler(snapshot);
      };
      ipcRenderer.on(IPC_CHANNELS.browserHostSnapshotChanged, listener);
      return () => {
        ipcRenderer.removeListener(IPC_CHANNELS.browserHostSnapshotChanged, listener);
      };
    }
  },
  shell: {
    openExternal: (url: string) => ipcRenderer.invoke(IPC_CHANNELS.openExternal, url),
    getFileIcon: (path: string) => ipcRenderer.invoke(IPC_CHANNELS.getFileIcon, path),
    showItemInFolder: (path: string) => ipcRenderer.invoke(IPC_CHANNELS.showItemInFolder, path)
  },
  notifications: {
    show: (payload: NotificationPayload): Promise<{ shown: boolean; reason?: string }> =>
      ipcRenderer.invoke(IPC_CHANNELS.showNotification, payload),
    onOpenTask: (handler: (taskId: string) => void): (() => void) => {
      const listener = (_event: Electron.IpcRendererEvent, taskId: unknown) => {
        if (typeof taskId === "string" && taskId.trim()) {
          handler(taskId);
        }
      };
      ipcRenderer.on(IPC_CHANNELS.openTaskFromNotification, listener);
      return () => {
        ipcRenderer.removeListener(IPC_CHANNELS.openTaskFromNotification, listener);
      };
    }
  },
  platform: preloadProcess?.platform ?? "win32",
  versions: {
    app: env.npm_package_version ?? "0.1.0",
    electron: version("electron"),
    chrome: version("chrome"),
    node: version("node")
  }
};

contextBridge.exposeInMainWorld("lengrvis", bridge);

function sanitizeApiBridgeRequest<TBody>(request: ApiRequest<TBody>): ApiRequest<TBody> {
  const requestRecord = clonePlainDataRecord(request, "Renderer API request");

  for (const key of Object.keys(requestRecord)) {
    if (!apiRequestAllowedKeys.has(key)) {
      throw new Error(`Renderer API request field is not allowed: ${key}`);
    }
  }

  if (typeof requestRecord.endpoint !== "string") {
    throw new Error("Renderer API endpoint is required");
  }

  const sanitized: ApiRequest<TBody> = { endpoint: requestRecord.endpoint };
  if (requestRecord.method !== undefined) {
    sanitized.method = sanitizeApiMethod(requestRecord.method);
  }
  if (requestRecord.query !== undefined) {
    sanitized.query = sanitizeApiQuery(requestRecord.query);
  }
  if (Object.prototype.hasOwnProperty.call(requestRecord, "body")) {
    sanitized.body = (
      requestRecord.body === undefined
        ? undefined
        : sanitizeApiBodyValue(requestRecord.body, "Renderer API body", 0, new WeakSet<object>())
    ) as TBody;
  }
  if (requestRecord.timeoutMs !== undefined) {
    sanitized.timeoutMs = sanitizeApiTimeout(requestRecord.timeoutMs);
  }
  if (requestRecord.abortGroup !== undefined) {
    sanitized.abortGroup = sanitizeApiAbortGroup(requestRecord.abortGroup);
  }
  return sanitized;
}

function sanitizeApiAbortGroup(value: unknown): string {
  if (typeof value !== "string") {
    throw new Error("Renderer API abort group is invalid");
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 64 || !/^[A-Za-z0-9._-]+$/.test(trimmed)) {
    throw new Error("Renderer API abort group is invalid");
  }
  return trimmed;
}

function sanitizeApiMethod(value: unknown): ApiMethod {
  if (typeof value !== "string" || !apiRequestAllowedMethods.has(value as ApiMethod)) {
    throw new Error("Renderer API request method is not allowed");
  }
  return value as ApiMethod;
}

function sanitizeApiTimeout(value: unknown): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    !Number.isInteger(value) ||
    value <= 0 ||
    value > API_REQUEST_SECURITY_LIMITS.maxTimeoutMs
  ) {
    throw new Error("Renderer API timeout is invalid");
  }
  return value;
}

function sanitizeApiQuery(value: unknown): Record<string, ApiQueryValue> {
  const queryRecord = clonePlainDataRecord(value, "Renderer API query");
  const entries = Object.entries(queryRecord);
  if (entries.length > API_REQUEST_SECURITY_LIMITS.maxQueryParams) {
    throw new Error("Renderer API query has too many parameters");
  }

  const query: Record<string, ApiQueryValue> = {};
  let totalChars = 0;
  for (const [key, queryValue] of entries) {
    assertSafeApiFieldName(key, "Renderer API query key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (queryValue === null || queryValue === undefined) {
      query[key] = queryValue;
      continue;
    }
    if (!["string", "number", "boolean"].includes(typeof queryValue)) {
      throw new Error("Renderer API query values must be primitive");
    }
    if (typeof queryValue === "number" && !Number.isFinite(queryValue)) {
      throw new Error("Renderer API query number is invalid");
    }
    const stringValue = String(queryValue);
    if (stringValue.length > API_REQUEST_SECURITY_LIMITS.maxQueryValueChars) {
      throw new Error("Renderer API query value is too large");
    }
    totalChars += key.length + stringValue.length;
    query[key] = queryValue as ApiQueryValue;
  }

  if (totalChars > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new Error("Renderer API query is too large");
  }

  return query;
}

type SanitizedApiBodyValue =
  | string
  | number
  | boolean
  | null
  | SanitizedApiBodyValue[]
  | { [key: string]: SanitizedApiBodyValue };

function sanitizeApiBodyValue(
  value: unknown,
  label: string,
  depth: number,
  seen: WeakSet<object>
): SanitizedApiBodyValue {
  if (depth > API_REQUEST_SECURITY_LIMITS.maxBodyDepth) {
    throw new Error("Renderer API body is too deeply nested");
  }

  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("Renderer API body number is invalid");
    }
    return value;
  }
  if (typeof value !== "object") {
    throw new Error("Renderer API body must be plain JSON data");
  }
  if (seen.has(value)) {
    throw new Error("Renderer API body cannot be circular");
  }

  seen.add(value);
  try {
    if (Array.isArray(value)) {
      return sanitizeApiBodyArray(value, label, depth, seen);
    }
    const record = clonePlainDataRecord(value, label);
    const entries = Object.entries(record);
    if (entries.length > API_REQUEST_SECURITY_LIMITS.maxBodyObjectKeys) {
      throw new Error("Renderer API body object has too many keys");
    }

    const sanitized: { [key: string]: SanitizedApiBodyValue } = {};
    for (const [key, item] of entries) {
      assertSafeApiFieldName(key, "Renderer API body key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
      sanitized[key] = sanitizeApiBodyValue(item, `${label}.${key}`, depth + 1, seen);
    }
    return sanitized;
  } finally {
    seen.delete(value);
  }
}

function sanitizeApiBodyArray(
  value: unknown[],
  label: string,
  depth: number,
  seen: WeakSet<object>
): SanitizedApiBodyValue[] {
  if (value.length > API_REQUEST_SECURITY_LIMITS.maxBodyArrayItems) {
    throw new Error("Renderer API body array is too large");
  }
  rejectUnexpectedArrayFields(value, label);

  const sanitized: SanitizedApiBodyValue[] = [];
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(value, index)) {
      throw new Error("Renderer API body array must not be sparse");
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
    if (!descriptor || !descriptor.enumerable || !("value" in descriptor)) {
      throw new Error("Renderer API body array must contain data values");
    }
    sanitized.push(sanitizeApiBodyValue(descriptor.value, `${label}[${index}]`, depth + 1, seen));
  }
  return sanitized;
}

function clonePlainDataRecord(value: unknown, label: string): Record<string, unknown> {
  if (!isPlainRecord(value)) {
    throw new Error(`${label} must be a plain object`);
  }

  const sanitized: Record<string, unknown> = {};
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key === "symbol") {
      throw new Error(`${label} must not contain symbol keys`);
    }
    assertSafeApiFieldName(key, `${label} key`, API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor) {
      continue;
    }
    if (!descriptor.enumerable) {
      throw new Error(`${label} must not contain non-enumerable fields`);
    }
    if (!("value" in descriptor)) {
      throw new Error(`${label} must not contain accessor fields`);
    }
    sanitized[key] = descriptor.value;
  }
  return sanitized;
}

function rejectUnexpectedArrayFields(value: unknown[], label: string): void {
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key === "symbol") {
      throw new Error(`${label} must not contain symbol keys`);
    }
    if (key === "length") {
      continue;
    }
    const index = Number(key);
    if (!Number.isInteger(index) || index < 0 || index >= value.length || String(index) !== key) {
      throw new Error("Renderer API body array must not contain object fields");
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !descriptor.enumerable || !("value" in descriptor)) {
      throw new Error("Renderer API body array must contain data values");
    }
  }
}

function assertSafeApiFieldName(name: string, label: string, maxChars: number): void {
  if (!name || name.length > maxChars || /[\u0000-\u001F\u007F]/.test(name) || apiRequestReservedKeys.has(name)) {
    throw new Error(`${label} is invalid`);
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function subscribeDesktopWebSocket(
  request: DesktopWebSocketSubscribeRequest,
  handlers: DesktopWebSocketSubscribeHandlers
): () => void {
  const id = nextDesktopWebSocketId();
  let closed = false;

  const cleanup = () => {
    ipcRenderer.removeListener(IPC_CHANNELS.desktopWebSocketEvent, listener);
  };

  const listener = (_event: Electron.IpcRendererEvent, payload: DesktopWebSocketBridgeEvent) => {
    if (!payload || payload.id !== id) {
      return;
    }

    if (payload.type === "open") {
      handlers.onOpen?.();
    } else if (payload.type === "message") {
      handlers.onMessage?.(payload.data);
    } else if (payload.type === "error") {
      handlers.onError?.({ message: payload.message });
    } else if (payload.type === "close") {
      closed = true;
      cleanup();
      handlers.onClose?.({
        code: payload.code,
        reason: payload.reason,
        wasClean: payload.wasClean
      });
    }
  };

  ipcRenderer.on(IPC_CHANNELS.desktopWebSocketEvent, listener);
  void ipcRenderer.invoke(IPC_CHANNELS.desktopWebSocketOpen, { ...request, id })
    .then((result: DesktopWebSocketOpenResult) => {
      if (closed) {
        void ipcRenderer.invoke(IPC_CHANNELS.desktopWebSocketClose, id).catch(() => undefined);
        return;
      }
      if (!result.ok) {
        closed = true;
        cleanup();
        handlers.onError?.({ message: result.error });
        handlers.onClose?.({ code: 1006, reason: result.error, wasClean: false });
      }
    })
    .catch((error: unknown) => {
      if (closed) {
        return;
      }
      closed = true;
      cleanup();
      handlers.onError?.({ message: error instanceof Error ? error.message : "Desktop WebSocket open failed" });
      handlers.onClose?.({ code: 1006, reason: "Desktop WebSocket open failed", wasClean: false });
    });

  return () => {
    if (closed) {
      return;
    }
    closed = true;
    cleanup();
    void ipcRenderer.invoke(IPC_CHANNELS.desktopWebSocketClose, id).catch(() => undefined);
  };
}

function nextDesktopWebSocketId(): string {
  desktopWebSocketSequence += 1;
  const random = Math.random().toString(36).slice(2, 10);
  return `ws_${Date.now().toString(36)}_${desktopWebSocketSequence.toString(36)}_${random}`;
}
