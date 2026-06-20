import { ipcRenderer, type IpcRendererEvent } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  DesktopWebSocketBridgeEvent,
  DesktopWebSocketOpenResult,
  DesktopWebSocketSubscribeHandlers,
  DesktopWebSocketSubscribeRequest
} from "../shared/types";

let desktopWebSocketSequence = 0;

export function subscribeDesktopWebSocket(
  request: DesktopWebSocketSubscribeRequest,
  handlers: DesktopWebSocketSubscribeHandlers
): () => void {
  const id = nextDesktopWebSocketId();
  let closed = false;

  const cleanup = () => {
    ipcRenderer.removeListener(IPC_CHANNELS.desktopWebSocketEvent, listener);
  };

  const listener = (_event: IpcRendererEvent, payload: DesktopWebSocketBridgeEvent) => {
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
