import { ipcMain, type IpcMainInvokeEvent, type WebContents } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  DesktopWebSocketBridgeEvent,
  DesktopWebSocketOpenRequest,
  DesktopWebSocketOpenResult,
  DesktopWebSocketSubscribeRequest
} from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import { assertTrustedRenderer } from "./ipc";

export const DESKTOP_WS_PROTOCOL_PREFIX = "mavris.desktop.token.";

interface DesktopSocketEntry {
  sender: WebContents;
  socket: WebSocket;
  onSenderDestroyed: () => void;
}

export function registerDesktopWebSocketIpcHandlers(backend: BackendProcessManager): void {
  const sockets = new Map<string, DesktopSocketEntry>();

  ipcMain.handle(
    IPC_CHANNELS.desktopWebSocketOpen,
    (event, request: DesktopWebSocketOpenRequest): DesktopWebSocketOpenResult => {
      assertTrustedRenderer(event);
      const socketId = validSocketId(request?.id);
      if (!socketId) {
        return { ok: false, id: "", error: "Desktop WebSocket id is invalid" };
      }

      closeDesktopSocket(sockets, socketId);

      try {
        const socket = createDesktopWebSocket(
          buildBackendWebSocketUrl(backend.getBaseUrl(), request.endpoint, request.query),
          backend.getDesktopApiToken()
        );
        const sender = event.sender;
        const onSenderDestroyed = () => closeDesktopSocket(sockets, socketId);
        sockets.set(socketId, { sender, socket, onSenderDestroyed });
        sender.once("destroyed", onSenderDestroyed);

        socket.addEventListener("open", () => {
          sendDesktopWebSocketEvent(sender, { id: socketId, type: "open" });
        });
        socket.addEventListener("message", (message) => {
          sendDesktopWebSocketEvent(sender, {
            id: socketId,
            type: "message",
            data: stringifyWebSocketData(message.data)
          });
        });
        socket.addEventListener("error", () => {
          sendDesktopWebSocketEvent(sender, { id: socketId, type: "error", message: "Desktop WebSocket error" });
        });
        socket.addEventListener("close", (closeEvent) => {
          const current = sockets.get(socketId);
          if (current?.socket === socket) {
            sockets.delete(socketId);
            sender.removeListener("destroyed", current.onSenderDestroyed);
          }
          sendDesktopWebSocketEvent(sender, {
            id: socketId,
            type: "close",
            code: closeEvent.code,
            reason: closeEvent.reason,
            wasClean: closeEvent.wasClean
          });
        });

        return { ok: true, id: socketId };
      } catch (error) {
        return {
          ok: false,
          id: socketId,
          error: error instanceof Error ? error.message : "Desktop WebSocket connection failed"
        };
      }
    }
  );

  ipcMain.handle(IPC_CHANNELS.desktopWebSocketClose, (event, socketId: unknown) => {
    assertTrustedRenderer(event);
    closeDesktopSocket(sockets, validSocketId(socketId));
  });
}

export function createDesktopWebSocket(url: string, desktopApiToken: string): WebSocket {
  return new WebSocket(url, desktopWebSocketProtocols(desktopApiToken));
}

export function desktopWebSocketProtocols(desktopApiToken: string): string[] | undefined {
  const token = desktopApiToken.trim();
  return token ? [`${DESKTOP_WS_PROTOCOL_PREFIX}${token}`] : undefined;
}

export function buildBackendWebSocketUrl(
  baseUrl: string,
  endpoint: string,
  query?: DesktopWebSocketSubscribeRequest["query"]
): string {
  if (!endpoint || typeof endpoint !== "string") {
    throw new Error("Desktop WebSocket endpoint is required");
  }
  if (
    !endpoint.startsWith("/") ||
    endpoint.startsWith("//") ||
    endpoint.includes("\\") ||
    /^[a-z][a-z0-9+.-]*:/i.test(endpoint)
  ) {
    throw new Error("Desktop WebSocket endpoints must be backend-relative");
  }

  const backendUrl = new URL(baseUrl);
  if (!["http:", "https:"].includes(backendUrl.protocol)) {
    throw new Error("Desktop WebSocket backend baseUrl must be HTTP(S)");
  }

  const url = new URL(endpoint, backendUrl);
  if (url.origin !== backendUrl.origin) {
    throw new Error("Desktop WebSocket endpoint escaped the configured backend origin");
  }

  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }

  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function closeDesktopSocket(sockets: Map<string, DesktopSocketEntry>, socketId?: string): void {
  if (!socketId) {
    return;
  }
  const entry = sockets.get(socketId);
  if (!entry) {
    return;
  }
  sockets.delete(socketId);
  entry.sender.removeListener("destroyed", entry.onSenderDestroyed);
  try {
    entry.socket.close();
  } catch {
    // Closing is best-effort during teardown.
  }
}

function sendDesktopWebSocketEvent(sender: WebContents, payload: DesktopWebSocketBridgeEvent): void {
  if (!sender.isDestroyed()) {
    sender.send(IPC_CHANNELS.desktopWebSocketEvent, payload);
  }
}

function validSocketId(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return /^[a-zA-Z0-9_-]{8,120}$/.test(trimmed) ? trimmed : undefined;
}

function stringifyWebSocketData(data: unknown): string {
  if (typeof data === "string") {
    return data;
  }
  if (Buffer.isBuffer(data)) {
    return data.toString("utf8");
  }
  if (data instanceof ArrayBuffer) {
    return Buffer.from(data).toString("utf8");
  }
  if (ArrayBuffer.isView(data)) {
    return Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString("utf8");
  }
  return data === undefined || data === null ? "" : String(data);
}
