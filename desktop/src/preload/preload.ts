import { contextBridge, ipcRenderer } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
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
  LengrvisDesktopBridge,
  NotificationPayload
} from "../shared/types";

const preloadProcess = typeof process === "undefined" ? null : process;
const env = preloadProcess?.env ?? {};
const version = (name: keyof NodeJS.ProcessVersions): string => preloadProcess?.versions?.[name] ?? "";
let desktopWebSocketSequence = 0;

function envValue(name: string, fallback = ""): string {
  const value = env[name];
  if (value) return value;
  return fallback;
}

const bridge: LengrvisDesktopBridge = {
  api: {
    request: <TResponse = unknown, TBody = unknown>(
      request: ApiRequest<TBody>
    ): Promise<ApiResponse<TResponse>> => ipcRenderer.invoke(IPC_CHANNELS.apiRequest, request)
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

contextBridge.exposeInMainWorld("lengrvis", bridge);

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
