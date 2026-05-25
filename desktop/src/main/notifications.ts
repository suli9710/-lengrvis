import { BrowserWindow, Notification, ipcMain } from "electron";
import { randomBytes } from "node:crypto";
import { createConnection, type Socket } from "node:net";
import { connect as connectTls, type TLSSocket } from "node:tls";

import { IPC_CHANNELS } from "../shared/ipc";
import type { NotificationPayload } from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";

const SYSTEM_NOTIFICATION_TASK_ID = "__system__";
const RECONNECT_DELAY_MS = 5_000;
const DEFAULT_NOTIFICATION_WS_PATHS = [
  "/ws/notifications",
  "/api/ws/notifications",
  `/ws/tasks/${encodeURIComponent(SYSTEM_NOTIFICATION_TASK_ID)}`,
  `/api/ws/tasks/${encodeURIComponent(SYSTEM_NOTIFICATION_TASK_ID)}`
] as const;

interface NotificationBridgeOptions {
  backend: BackendProcessManager;
  getMainWindow: () => BrowserWindow | null;
}

interface NotificationResult {
  shown: boolean;
  reason?: string;
}

interface NotificationSocket {
  readyState: number;
  onmessage: ((event: { data: unknown }) => void) | null;
  onerror: ((error?: unknown) => void) | null;
  onclose: (() => void) | null;
  close: () => void;
}

interface NotificationSocketConstructor {
  CONNECTING: number;
  OPEN: number;
  new (url: string): NotificationSocket;
}

export class NotificationBridge {
  private reconnectTimer: NodeJS.Timeout | null = null;
  private socket: NotificationSocket | null = null;
  private socketPathIndex = 0;
  private socketReceivedMessage = false;
  private stopped = true;

  constructor(private readonly options: NotificationBridgeOptions) {}

  registerIpcHandlers(): void {
    ipcMain.handle(IPC_CHANNELS.showNotification, (_event, payload: unknown, legacyBody?: unknown) => {
      const notification = normalizeNotificationPayload(payload, legacyBody);
      if (!notification) {
        return { shown: false, reason: "invalid_payload" } satisfies NotificationResult;
      }

      return this.show(notification);
    });
  }

  startBackendListener(): void {
    if (!this.stopped) {
      return;
    }

    this.stopped = false;
    this.connectBackendSocket();
  }

  stopBackendListener(): void {
    this.stopped = true;
    this.clearReconnectTimer();
    this.socket?.close();
    this.socket = null;
  }

  show(payload: NotificationPayload): NotificationResult {
    if (!Notification.isSupported()) {
      return { shown: false, reason: "unsupported" };
    }

    const notification = new Notification({
      title: payload.title,
      body: payload.body,
      urgency: urgencyForSeverity(payload.severity)
    });

    notification.on("click", () => {
      this.openTaskView(payload.taskId);
    });
    notification.show();

    return { shown: true };
  }

  private connectBackendSocket(): void {
    const WebSocketCtor = getWebSocketConstructor();

    if (this.stopped || this.socket?.readyState === WebSocketCtor.CONNECTING || this.socket?.readyState === WebSocketCtor.OPEN) {
      return;
    }

    let socket: NotificationSocket;
    try {
      socket = new WebSocketCtor(
        buildBackendNotificationWebSocketUrl(
          this.options.backend.getBaseUrl(),
          this.currentBackendNotificationWebSocketPath()
        )
      );
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.socket = socket;
    this.socketReceivedMessage = false;

    socket.onmessage = (event) => {
      this.socketReceivedMessage = true;
      const payload = notificationPayloadFromSocketEvent(event.data);
      if (payload) {
        this.show(payload);
      }
    };

    socket.onerror = () => {
      // The close handler owns reconnect scheduling.
    };

    socket.onclose = () => {
      if (this.socket === socket) {
        this.socket = null;
      }
      if (!this.socketReceivedMessage && !hasConfiguredNotificationWebSocket() && this.socketPathIndex < DEFAULT_NOTIFICATION_WS_PATHS.length - 1) {
        this.socketPathIndex += 1;
      }
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) {
      return;
    }

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connectBackendSocket();
    }, RECONNECT_DELAY_MS);
  }

  private currentBackendNotificationWebSocketPath(): string {
    const configuredPath = process.env.MAVRIS_NOTIFICATION_WS_PATH;
    if (configuredPath) {
      return configuredPath;
    }

    return DEFAULT_NOTIFICATION_WS_PATHS[this.socketPathIndex] ?? DEFAULT_NOTIFICATION_WS_PATHS[0];
  }

  private clearReconnectTimer(): void {
    if (!this.reconnectTimer) {
      return;
    }

    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private openTaskView(taskId?: string): void {
    if (!taskId || taskId === SYSTEM_NOTIFICATION_TASK_ID) {
      return;
    }

    const window = this.options.getMainWindow() ?? BrowserWindow.getAllWindows()[0] ?? null;
    if (!window) {
      return;
    }

    if (window.isMinimized()) {
      window.restore();
    }
    window.show();
    window.focus();

    const sendOpenTask = () => {
      window.webContents.send(IPC_CHANNELS.openTaskFromNotification, taskId);
    };

    if (window.webContents.isLoading()) {
      window.webContents.once("did-finish-load", sendOpenTask);
    } else {
      sendOpenTask();
    }
  }
}

function buildBackendNotificationWebSocketUrl(baseUrl: string, path: string): string {
  const configuredUrl = process.env.MAVRIS_NOTIFICATION_WS_URL;
  if (configuredUrl && /^wss?:\/\//i.test(configuredUrl)) {
    return configuredUrl;
  }

  const url = new URL(path, baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function hasConfiguredNotificationWebSocket(): boolean {
  return Boolean(process.env.MAVRIS_NOTIFICATION_WS_URL || process.env.MAVRIS_NOTIFICATION_WS_PATH);
}

function getWebSocketConstructor(): NotificationSocketConstructor {
  return typeof globalThis.WebSocket === "function"
    ? globalThis.WebSocket as unknown as NotificationSocketConstructor
    : NodeNotificationSocket;
}

class NodeNotificationSocket implements NotificationSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: ((error?: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = NodeNotificationSocket.CONNECTING;

  private frameBuffer = Buffer.alloc(0);
  private handshakeBuffer = Buffer.alloc(0);
  private isHandshakeComplete = false;
  private socket: Socket | TLSSocket | null = null;

  constructor(url: string) {
    this.connect(url);
  }

  close(): void {
    if (this.readyState === NodeNotificationSocket.CLOSED) {
      return;
    }

    this.readyState = NodeNotificationSocket.CLOSING;
    this.socket?.end();
    this.socket?.destroy();
    this.finishClose();
  }

  private connect(rawUrl: string): void {
    let url: URL;
    try {
      url = new URL(rawUrl);
    } catch (error) {
      this.fail(error);
      return;
    }

    const isSecure = url.protocol === "wss:";
    const port = Number(url.port || (isSecure ? 443 : 80));
    const socket = isSecure
      ? connectTls({ host: url.hostname, port, servername: url.hostname })
      : createConnection({ host: url.hostname, port });

    this.socket = socket;
    socket.setNoDelay(true);
    const connectedEvent = isSecure ? "secureConnect" : "connect";
    socket.once(connectedEvent, () => {
      socket.write(buildWebSocketHandshake(url));
    });
    socket.on("data", (chunk) => {
      this.handleData(chunk);
    });
    socket.on("error", (error) => {
      this.fail(error);
    });
    socket.on("close", () => {
      this.finishClose();
    });
  }

  private handleData(chunk: Buffer): void {
    if (!this.isHandshakeComplete) {
      this.handshakeBuffer = Buffer.concat([this.handshakeBuffer, chunk]);
      const headerEnd = this.handshakeBuffer.indexOf("\r\n\r\n");
      if (headerEnd < 0) {
        return;
      }

      const header = this.handshakeBuffer.subarray(0, headerEnd).toString("utf8");
      const remaining = this.handshakeBuffer.subarray(headerEnd + 4);
      this.handshakeBuffer = Buffer.alloc(0);
      if (!/^HTTP\/1\.[01] 101\b/.test(header)) {
        this.fail(new Error("Notification WebSocket handshake failed"));
        return;
      }

      this.isHandshakeComplete = true;
      this.readyState = NodeNotificationSocket.OPEN;
      if (remaining.length) {
        this.frameBuffer = Buffer.concat([this.frameBuffer, remaining]);
      }
    } else {
      this.frameBuffer = Buffer.concat([this.frameBuffer, chunk]);
    }

    this.readFrames();
  }

  private readFrames(): void {
    while (this.frameBuffer.length >= 2) {
      const firstByte = this.frameBuffer[0];
      const secondByte = this.frameBuffer[1];
      const opcode = firstByte & 0x0f;
      const masked = Boolean(secondByte & 0x80);
      let payloadLength = secondByte & 0x7f;
      let offset = 2;

      if (payloadLength === 126) {
        if (this.frameBuffer.length < offset + 2) return;
        payloadLength = this.frameBuffer.readUInt16BE(offset);
        offset += 2;
      } else if (payloadLength === 127) {
        if (this.frameBuffer.length < offset + 8) return;
        const highBits = this.frameBuffer.readUInt32BE(offset);
        if (highBits !== 0) {
          this.fail(new Error("Notification WebSocket frame is too large"));
          return;
        }
        payloadLength = this.frameBuffer.readUInt32BE(offset + 4);
        offset += 8;
      }

      const maskLength = masked ? 4 : 0;
      if (this.frameBuffer.length < offset + maskLength + payloadLength) {
        return;
      }

      const mask = masked ? this.frameBuffer.subarray(offset, offset + 4) : null;
      offset += maskLength;
      const payload = Buffer.from(this.frameBuffer.subarray(offset, offset + payloadLength));
      this.frameBuffer = this.frameBuffer.subarray(offset + payloadLength);

      if (mask) {
        for (let index = 0; index < payload.length; index += 1) {
          payload[index] ^= mask[index % 4];
        }
      }

      if (opcode === 0x1) {
        this.onmessage?.({ data: payload.toString("utf8") });
      } else if (opcode === 0x8) {
        this.close();
        return;
      }
    }
  }

  private fail(error: unknown): void {
    this.onerror?.(error);
    this.close();
  }

  private finishClose(): void {
    if (this.readyState === NodeNotificationSocket.CLOSED) {
      return;
    }

    this.readyState = NodeNotificationSocket.CLOSED;
    this.socket = null;
    this.onclose?.();
  }
}

function buildWebSocketHandshake(url: URL): string {
  const key = randomBytes(16).toString("base64");
  const path = `${url.pathname || "/"}${url.search}`;
  const host = url.port ? `${url.hostname}:${url.port}` : url.hostname;

  return [
    `GET ${path} HTTP/1.1`,
    `Host: ${host}`,
    "Upgrade: websocket",
    "Connection: Upgrade",
    `Sec-WebSocket-Key: ${key}`,
    "Sec-WebSocket-Version: 13",
    "\r\n"
  ].join("\r\n");
}

function notificationPayloadFromSocketEvent(data: unknown): NotificationPayload | null {
  const event = parseSocketEvent(data);
  if (!event) {
    return null;
  }

  if (event.type === "notification") {
    return normalizeNotificationPayload(event.payload ?? event);
  }

  const message = asRecord(event.message);
  if (!message) {
    return notificationMessageType(event) === "notification"
      ? normalizeNotificationPayload({
          ...event,
          body: event.body ?? event.content,
          taskId: event.taskId ?? event.task_id
        })
      : null;
  }

  if (notificationMessageType(message) !== "notification") {
    return null;
  }

  const metadata = asRecord(message.metadata);
  const structuredPayload = asRecord(message.structured_payload) ?? asRecord(metadata?.structured_payload) ?? {};

  return normalizeNotificationPayload({
    ...structuredPayload,
    title: structuredPayload.title ?? message.title,
    body: structuredPayload.body ?? message.body ?? message.content,
    severity: structuredPayload.severity ?? message.severity,
    taskId: structuredPayload.taskId ?? structuredPayload.task_id ?? message.taskId ?? message.task_id ?? event.task_id
  });
}

function parseSocketEvent(data: unknown): Record<string, unknown> | null {
  if (typeof data !== "string") {
    return null;
  }

  try {
    return asRecord(JSON.parse(data));
  } catch {
    return null;
  }
}

function normalizeNotificationPayload(payload: unknown, legacyBody?: unknown): NotificationPayload | null {
  if (typeof payload === "string") {
    return makeNotificationPayload({
      title: payload,
      body: typeof legacyBody === "string" ? legacyBody : "",
      severity: "info"
    });
  }

  const raw = asRecord(payload);
  if (!raw) {
    return null;
  }

  const nestedPayload = asRecord(raw.payload);
  const source = nestedPayload ?? raw;
  return makeNotificationPayload({
    title: source.title,
    body: source.body,
    taskId: source.taskId ?? source.task_id,
    severity: source.severity
  });
}

function makeNotificationPayload(raw: Record<string, unknown>): NotificationPayload | null {
  const title = stringValue(raw.title) || "Mavris";
  const body = stringValue(raw.body);

  if (!title || !body) {
    return null;
  }

  const taskId = stringValue(raw.taskId);
  return {
    title,
    body,
    severity: normalizeSeverity(raw.severity),
    ...(taskId ? { taskId } : {})
  };
}

function notificationMessageType(value: Record<string, unknown>): string {
  const metadata = asRecord(value.metadata);
  return stringValue(value.message_type ?? metadata?.message_type ?? value.type).toLowerCase();
}

function normalizeSeverity(value: unknown): NotificationPayload["severity"] {
  return value === "warning" || value === "error" || value === "info" ? value : "info";
}

function urgencyForSeverity(severity: NotificationPayload["severity"]): "normal" | "critical" | "low" {
  if (severity === "error") {
    return "critical";
  }
  if (severity === "info") {
    return "low";
  }
  return "normal";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
