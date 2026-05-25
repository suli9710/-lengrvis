import { contextBridge, ipcRenderer } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type { ApiRequest, ApiResponse, MavrisDesktopBridge, NotificationPayload } from "../shared/types";

const bridge: MavrisDesktopBridge = {
  api: {
    request: <TResponse = unknown, TBody = unknown>(
      request: ApiRequest<TBody>
    ): Promise<ApiResponse<TResponse>> => ipcRenderer.invoke(IPC_CHANNELS.apiRequest, request)
  },
  backend: {
    getStatus: () => ipcRenderer.invoke(IPC_CHANNELS.backendStatus),
    start: () => ipcRenderer.invoke(IPC_CHANNELS.backendStart),
    stop: () => ipcRenderer.invoke(IPC_CHANNELS.backendStop)
  },
  backendBaseUrl: process.env.MAVRIS_BACKEND_URL ?? "http://127.0.0.1:8000",
  dialog: {
    chooseSkillDirectory: () => ipcRenderer.invoke(IPC_CHANNELS.chooseSkillDirectory),
    chooseSkillZip: () => ipcRenderer.invoke(IPC_CHANNELS.chooseSkillZip)
  },
  shell: {
    openExternal: (url: string) => ipcRenderer.invoke(IPC_CHANNELS.openExternal, url)
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
  platform: process.platform,
  versions: {
    app: process.env.npm_package_version ?? "0.1.0",
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node
  }
};

contextBridge.exposeInMainWorld("mavris", bridge);
