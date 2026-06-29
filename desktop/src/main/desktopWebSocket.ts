import { ipcMain, type IpcMainInvokeEvent, type WebContents } from "electron";

import { API_REQUEST_SECURITY_LIMITS, IPC_CHANNELS } from "../shared/ipc";
import type {
  ApiQueryValue,
  DesktopWebSocketBridgeEvent,
  DesktopWebSocketOpenRequest,
  DesktopWebSocketOpenResult,
  DesktopWebSocketSubscribeRequest
} from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import { assertLoopbackBackendUrl } from "./backendUrl";
import { assertTrustedRenderer } from "./ipc";

export const DESKTOP_WS_PROTOCOL_PREFIX = "lengrvis.desktop.token.";
const WEB_SOCKET_PROTOCOL_TOKEN_REGEX = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
const DESKTOP_WS_RESERVED_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const PASSIVE_DESKTOP_WS_PATTERNS = [
  /^\/(?:api\/)?ws\/notifications$/,
  /^\/(?:api\/)?ws\/tasks\/[^/]+$/,
  /^\/(?:api\/)?ws\/runs\/[^/]+$/,
  /^\/api\/ws\/browser-host$/
];

interface DesktopSocketEntry {
  sender: WebContents;
  socket: WebSocket;
  onSenderDestroyed: () => void;
}

type ValidatedDesktopWebSocketQuery = Record<string, Exclude<ApiQueryValue, null | undefined>>;

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

export function desktopWebSocketProtocols(desktopApiToken: string): [string] {
  const token = desktopApiToken.trim();
  if (!token) {
    throw new Error("Desktop WebSocket token is required");
  }

  const protocol = `${DESKTOP_WS_PROTOCOL_PREFIX}${token}`;
  if (!WEB_SOCKET_PROTOCOL_TOKEN_REGEX.test(protocol)) {
    throw new Error("Desktop WebSocket token cannot be used as a WebSocket subprotocol");
  }

  return [protocol];
}

export function buildBackendWebSocketUrl(
  baseUrl: string,
  endpoint: string,
  query?: DesktopWebSocketSubscribeRequest["query"]
): string {
  const safeEndpoint = validateDesktopWebSocketEndpoint(endpoint);
  const safeQuery = validateDesktopWebSocketQuery(query);

  const backendUrl = assertLoopbackBackendUrl(baseUrl, "Desktop WebSocket");

  const url = new URL(safeEndpoint, backendUrl);
  if (url.origin !== backendUrl.origin) {
    throw new Error("Desktop WebSocket endpoint escaped the configured backend origin");
  }

  for (const [key, value] of Object.entries(safeQuery ?? {})) {
    url.searchParams.set(key, String(value));
  }

  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function validateDesktopWebSocketEndpoint(endpoint: unknown): string {
  if (typeof endpoint !== "string") {
    throw new Error("Desktop WebSocket endpoint is required");
  }
  if (!endpoint || endpoint.length > API_REQUEST_SECURITY_LIMITS.maxEndpointChars) {
    throw new Error("Desktop WebSocket endpoint length is invalid");
  }
  if (endpoint.trim() !== endpoint || /\s|[\u0000-\u001F\u007F]/.test(endpoint)) {
    throw new Error("Desktop WebSocket endpoint contains unsafe characters");
  }
  if (endpoint.includes("?") || endpoint.includes("#")) {
    throw new Error("Desktop WebSocket endpoint must not include query strings or fragments");
  }
  if (
    !endpoint.startsWith("/") ||
    endpoint.startsWith("//") ||
    endpoint.includes("//") ||
    endpoint.includes("\\") ||
    /^[a-z][a-z0-9+.-]*:/i.test(endpoint)
  ) {
    throw new Error("Desktop WebSocket endpoints must be backend-relative");
  }
  if (/%2f|%5c/i.test(endpoint)) {
    throw new Error("Desktop WebSocket endpoint must not contain encoded path separators");
  }

  let decodedPath: string;
  try {
    decodedPath = decodeURIComponent(endpoint);
  } catch {
    throw new Error("Desktop WebSocket endpoint encoding is invalid");
  }
  if (decodedPath.includes("\\") || decodedPath.includes("//")) {
    throw new Error("Desktop WebSocket endpoint contains unsafe path separators");
  }
  if (decodedPath.split("/").some((segment) => segment === "." || segment === "..")) {
    throw new Error("Desktop WebSocket endpoint contains unsafe path segments");
  }
  if (!PASSIVE_DESKTOP_WS_PATTERNS.some((pattern) => pattern.test(decodedPath))) {
    throw new Error("Desktop WebSocket endpoint is not on the passive subscription allowlist");
  }

  return endpoint;
}

function validateDesktopWebSocketQuery(query: unknown): ValidatedDesktopWebSocketQuery | undefined {
  if (query === undefined) {
    return undefined;
  }
  if (!isPlainRecord(query)) {
    throw new Error("Desktop WebSocket query must be an object");
  }

  const entries = Object.entries(query);
  if (entries.length > API_REQUEST_SECURITY_LIMITS.maxQueryParams) {
    throw new Error("Desktop WebSocket query has too many parameters");
  }

  let totalBytes = 0;
  const safeQuery: ValidatedDesktopWebSocketQuery = {};
  for (const [key, value] of entries) {
    assertSafeWebSocketQueryKey(key);
    if (value === null || value === undefined) {
      continue;
    }
    if (!["string", "number", "boolean"].includes(typeof value)) {
      throw new Error("Desktop WebSocket query values must be primitive");
    }
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new Error("Desktop WebSocket query number is invalid");
    }
    const stringValue = String(value);
    const valueBytes = Buffer.byteLength(stringValue, "utf8");
    if (valueBytes > API_REQUEST_SECURITY_LIMITS.maxQueryValueChars) {
      throw new Error("Desktop WebSocket query value is too large");
    }
    totalBytes += Buffer.byteLength(key, "utf8") + valueBytes;
    safeQuery[key] = value as Exclude<ApiQueryValue, null | undefined>;
  }

  if (totalBytes > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new Error("Desktop WebSocket query is too large");
  }

  return Object.keys(safeQuery).length ? safeQuery : undefined;
}

function assertSafeWebSocketQueryKey(key: string): void {
  if (
    !key ||
    key.length > API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars ||
    /[\u0000-\u001F\u007F]/.test(key) ||
    DESKTOP_WS_RESERVED_KEYS.has(key)
  ) {
    throw new Error("Desktop WebSocket query key is invalid");
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
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
