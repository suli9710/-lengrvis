import { contextBridge, ipcRenderer } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  ApiRequest,
  ApiResponse,
  DesktopBrowserSessionRequest,
  DesktopCommerceLicenseActivateRequest,
  DesktopCommerceLicenseInstallRequest,
  DesktopCommercePolicyImportRequest,
  DesktopHardwareAccelerationSmokeRequest,
  DesktopMemoryRecallRequest,
  DesktopMemorySaveRequest,
  LengrvisDesktopBridge,
  DesktopPerceptionSuggestionLaunchRequest,
  DesktopPermissionPolicyRelaxationRequest,
  DesktopPermissionRuleDeleteRequest,
  DesktopPermissionRuleUpsertRequest,
  DesktopOpenSettingsRequest,
  DesktopPrivacyEraseRequest,
  DesktopRunStartRequest,
  DesktopScheduleCreateRequest,
  DesktopScheduleEnableRequest,
  DesktopSettingsPatch,
  CredentialFillRequest,
  CredentialRefRequest,
  CredentialSessionRequest,
  CredentialUseTicketRequest,
  MobilePairingRemoteInputGrantRequest,
  MobilePairingRevokeRemoteInputGrantRequest,
  NotificationPayload
} from "../shared/types";
import type {
  BrowserHostActionRequest,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot
} from "../shared/browserTypes";
import type { DocumentAskRequest, DocumentCompareRequest, DocumentParseRequest } from "../shared/documentTypes";
import type {
  AcceptConsentRequest,
  ConsentRecord,
  ConsentStatusResult,
  LegalDocId
} from "../shared/consent";
import { sanitizeApiAbortGroup, sanitizeApiBridgeRequest } from "./apiBridgeSanitizer";
import { subscribeDesktopWebSocket } from "./desktopWebSocketBridge";

const preloadProcess = typeof process === "undefined" ? null : process;
const env = preloadProcess?.env ?? {};
const version = (name: keyof NodeJS.ProcessVersions): string => preloadProcess?.versions?.[name] ?? "";

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
      } catch (error) { // broad-exception-boundary
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
  approvals: {
    approve: (approvalId: string) => ipcRenderer.invoke(IPC_CHANNELS.approvalApprove, approvalId),
    reject: (approvalId: string) => ipcRenderer.invoke(IPC_CHANNELS.approvalReject, approvalId)
  },
  tasks: {
    pause: (taskId: string) => ipcRenderer.invoke(IPC_CHANNELS.taskPause, taskId),
    resume: (taskId: string) => ipcRenderer.invoke(IPC_CHANNELS.taskResume, taskId),
    cancel: (taskId: string) => ipcRenderer.invoke(IPC_CHANNELS.taskCancel, taskId),
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
  perception: {
    launchSuggestion: (request: DesktopPerceptionSuggestionLaunchRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.perceptionSuggestionLaunch, request)
  },
  hardwareAcceleration: {
    smoke: (request: DesktopHardwareAccelerationSmokeRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.hardwareAccelerationSmoke, request)
  },
  browserBackend: {
    observe: (request: DesktopBrowserSessionRequest) => ipcRenderer.invoke(IPC_CHANNELS.browserObserve, request),
    replayExport: (request: DesktopBrowserSessionRequest) => ipcRenderer.invoke(IPC_CHANNELS.browserReplayExport, request)
  },
  commerce: {
    installLicense: (request: DesktopCommerceLicenseInstallRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.commerceLicenseInstall, request),
    activateLicense: (request: DesktopCommerceLicenseActivateRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.commerceLicenseActivate, request),
    importPolicy: (request: DesktopCommercePolicyImportRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.commercePolicyImport, request)
  },
  memories: {
    save: (request: DesktopMemorySaveRequest) => ipcRenderer.invoke(IPC_CHANNELS.memoriesSave, request),
    recall: (request: DesktopMemoryRecallRequest) => ipcRenderer.invoke(IPC_CHANNELS.memoriesRecall, request),
    forget: (memoryId: string) => ipcRenderer.invoke(IPC_CHANNELS.memoriesForget, memoryId)
  },
  schedules: {
    list: () => ipcRenderer.invoke(IPC_CHANNELS.schedulesList),
    create: (request: DesktopScheduleCreateRequest) => ipcRenderer.invoke(IPC_CHANNELS.schedulesCreate, request),
    delete: (scheduleId: string) => ipcRenderer.invoke(IPC_CHANNELS.schedulesDelete, scheduleId),
    enable: (request: DesktopScheduleEnableRequest) => ipcRenderer.invoke(IPC_CHANNELS.schedulesEnable, request)
  },
  system: {
    openSettings: (request: DesktopOpenSettingsRequest) => ipcRenderer.invoke(IPC_CHANNELS.systemOpenSettings, request),
    exportDiagnosticsPackage: () => ipcRenderer.invoke(IPC_CHANNELS.systemDiagnosticsExport)
  },
  privacy: {
    eraseLocalData: (request: DesktopPrivacyEraseRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.privacyEraseLocalData, request)
  },
  documents: {
    parse: (request: DocumentParseRequest) => ipcRenderer.invoke(IPC_CHANNELS.documentsParse, request),
    ask: (request: DocumentAskRequest) => ipcRenderer.invoke(IPC_CHANNELS.documentsAsk, request),
    compare: (request: DocumentCompareRequest) => ipcRenderer.invoke(IPC_CHANNELS.documentsCompare, request)
  },
  settings: {
    confirmSensitiveChange: (patch: DesktopSettingsPatch) =>
      ipcRenderer.invoke(IPC_CHANNELS.settingsConfirmSensitiveChange, patch),
    testLlmProvider: () => ipcRenderer.invoke(IPC_CHANNELS.settingsTestLlmProvider),
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
  consent: {
    getStatus: (): Promise<ConsentStatusResult> =>
      ipcRenderer.invoke(IPC_CHANNELS.consentStatus),
    accept: (request: AcceptConsentRequest): Promise<ConsentRecord> =>
      ipcRenderer.invoke(IPC_CHANNELS.consentAccept, request),
    readDoc: (docId: LegalDocId): Promise<{ content: string; docId: LegalDocId }> =>
      ipcRenderer.invoke(IPC_CHANNELS.consentReadDoc, docId)
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
  credentials: {
    listForSession: (request: CredentialSessionRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.credentialsListForSession, request),
    captureFromPage: (request: CredentialSessionRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.credentialsCaptureFromPage, request),
    issueUseTicket: (request: CredentialUseTicketRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.credentialsIssueUseTicket, request),
    fill: (request: CredentialFillRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.credentialsFill, request),
    delete: (request: CredentialRefRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.credentialsDelete, request)
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
    app: env.npm_package_version ?? "0.1.2",
    electron: version("electron"),
    chrome: version("chrome"),
    node: version("node")
  }
};

contextBridge.exposeInMainWorld("lengrvis", bridge);
