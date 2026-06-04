import { contextBridge, ipcRenderer } from "electron";

import type {
  ApiRequest,
  ApiResponse,
  BrowserHostActionRequest,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  MavrisDesktopBridge,
  NotificationPayload
} from "../shared/types";

const IPC_CHANNELS = {
  apiRequest: "mavris:api:request",
  backendStatus: "mavris:backend:status",
  backendStart: "mavris:backend:start",
  backendStop: "mavris:backend:stop",
  backendForeground: "mavris:backend:foreground",
  backendBackground: "mavris:backend:background",
  openExternal: "mavris:shell:open-external",
  getFileIcon: "mavris:shell:get-file-icon",
  chooseDirectory: "mavris:dialog:choose-directory",
  chooseDocument: "mavris:dialog:choose-document",
  knownFolders: "mavris:dialog:known-folders",
  chooseSkillDirectory: "mavris:dialog:choose-skill-directory",
  chooseSkillZip: "mavris:dialog:choose-skill-zip",
  browserHostSnapshot: "mavris:browser-host:snapshot",
  browserHostSnapshotChanged: "mavris:browser-host:snapshot-changed",
  browserHostOpen: "mavris:browser-host:open",
  browserHostShow: "mavris:browser-host:show",
  browserHostHide: "mavris:browser-host:hide",
  browserHostSetBounds: "mavris:browser-host:set-bounds",
  browserHostPause: "mavris:browser-host:pause",
  browserHostResume: "mavris:browser-host:resume",
  browserHostTakeover: "mavris:browser-host:takeover",
  browserHostRelease: "mavris:browser-host:release",
  browserHostStop: "mavris:browser-host:stop",
  browserHostAction: "mavris:browser-host:action",
  showNotification: "mavris:show-notification",
  openTaskFromNotification: "mavris:notification:open-task"
} as const;

const preloadProcess = typeof process === "undefined" ? null : process;
const env = preloadProcess?.env ?? {};
const version = (name: keyof NodeJS.ProcessVersions): string => preloadProcess?.versions?.[name] ?? "";

const bridge: MavrisDesktopBridge = {
  api: {
    request: <TResponse = unknown, TBody = unknown>(
      request: ApiRequest<TBody>
    ): Promise<ApiResponse<TResponse>> => ipcRenderer.invoke(IPC_CHANNELS.apiRequest, request)
  },
  backend: {
    getStatus: () => ipcRenderer.invoke(IPC_CHANNELS.backendStatus),
    start: () => ipcRenderer.invoke(IPC_CHANNELS.backendStart),
    stop: () => ipcRenderer.invoke(IPC_CHANNELS.backendStop),
    foreground: () => ipcRenderer.invoke(IPC_CHANNELS.backendForeground),
    background: () => ipcRenderer.invoke(IPC_CHANNELS.backendBackground)
  },
  backendBaseUrl: env.MAVRIS_BACKEND_URL ?? "http://127.0.0.1:8000",
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
    getFileIcon: (path: string) => ipcRenderer.invoke(IPC_CHANNELS.getFileIcon, path)
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

contextBridge.exposeInMainWorld("mavris", bridge);
