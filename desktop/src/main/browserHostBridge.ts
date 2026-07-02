import type {
  BrowserAction,
  BrowserHostActionResult,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot
} from "../shared/types";
import { sanitizeActionResultForRenderer } from "../shared/browserHostRedaction";
import { buildBackendWebSocketUrl, createDesktopWebSocket } from "./desktopWebSocket";

type BrowserHostBridgeTarget = {
  getSnapshot: () => BrowserHostSnapshot;
  hide: () => BrowserHostActionResult;
  onSnapshot: (listener: (snapshot: BrowserHostSnapshot) => void) => () => void;
  open: (request?: BrowserHostOpenRequest) => Promise<BrowserHostActionResult>;
  pause: (sessionId: string) => BrowserHostActionResult;
  performAction: (sessionId: string, action: BrowserAction) => Promise<BrowserHostActionResult>;
  release: (sessionId: string) => BrowserHostActionResult;
  resume: (sessionId: string) => BrowserHostActionResult;
  setBounds: (bounds: BrowserHostBounds) => BrowserHostActionResult;
  show: (sessionId: string) => BrowserHostActionResult;
  stop: (sessionId: string) => Promise<BrowserHostActionResult>;
};

export class BrowserHostWebSocketBridge {
  private socket: WebSocket | null = null;
  private retryTimer: NodeJS.Timeout | null = null;
  private stopped = true;
  private unsubscribeSnapshots: (() => void) | null = null;

  constructor(
    private readonly browserHost: BrowserHostBridgeTarget,
    private readonly getBaseUrl: () => string,
    private readonly getDesktopApiToken: () => string
  ) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.unsubscribeSnapshots = this.browserHost.onSnapshot((snapshot) => {
      this.send({ type: "snapshot", snapshot });
    });
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.unsubscribeSnapshots?.();
    this.unsubscribeSnapshots = null;
    this.socket?.close();
    this.socket = null;
  }

  private connect(): void {
    if (this.stopped || typeof WebSocket === "undefined") return;
    try {
      const desktopApiToken = this.getDesktopApiToken();
      this.socket = createDesktopWebSocket(buildBrowserHostWebSocketUrl(this.getBaseUrl()), desktopApiToken);
      this.socket.addEventListener("open", () => {
        this.send({ type: "snapshot", snapshot: this.browserHost.getSnapshot() });
      });
      this.socket.addEventListener("message", (event) => {
        void this.handleMessage(event.data);
      });
      this.socket.addEventListener("close", () => {
        this.socket = null;
        this.scheduleReconnect();
      });
      this.socket.addEventListener("error", () => {
        this.socket?.close();
      });
    } catch {
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.retryTimer) return;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, 2500);
  }

  private async handleMessage(rawData: unknown): Promise<void> {
    const message = parseBrowserHostBridgeMessage(rawData);
    if (!message) return;

    let result: BrowserHostActionResult;
    switch (message.type) {
      case "open":
        result = await this.browserHost.open({
          sessionId: message.session_id,
          taskId: message.task_id,
          url: message.url,
          title: message.title,
          mode: message.mode
        });
        break;
      case "show":
        result = this.browserHost.show(message.session_id);
        break;
      case "hide":
        result = this.browserHost.hide();
        break;
      case "set_bounds":
        result = this.browserHost.setBounds(message.bounds);
        break;
      case "pause":
        result = this.browserHost.pause(message.session_id);
        break;
      case "resume":
        result = this.browserHost.resume(message.session_id);
        break;
      case "takeover":
        result = this.deniedRemoteWriteAction("BrowserHost remote takeover requires a desktop approval grant.");
        break;
      case "release":
        result = this.browserHost.release(message.session_id);
        break;
      case "stop":
        result = await this.browserHost.stop(message.session_id);
        break;
      case "action":
        result = isReadOnlyBrowserHostAction(message.action)
          ? await this.browserHost.performAction(message.session_id, message.action)
          : this.deniedRemoteWriteAction("BrowserHost remote actions require a desktop approval grant.");
        break;
      default:
        result = { ok: true, snapshot: this.browserHost.getSnapshot() };
        break;
    }
    result = sanitizeActionResultForRenderer(result);

    this.send({
      type: "result",
      request_id: message.request_id,
      ok: result.ok,
      session: result.session,
      event: result.event,
      error: result.error
    });
  }

  private send(payload: unknown): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(payload));
  }

  private deniedRemoteWriteAction(error: string): BrowserHostActionResult {
    return {
      ok: false,
      error,
      snapshot: this.browserHost.getSnapshot()
    };
  }
}

export function isReadOnlyBrowserHostAction(action: BrowserAction | undefined): action is BrowserAction {
  return action?.kind === "observe" || action?.kind === "screenshot";
}

export function buildBrowserHostWebSocketUrl(baseUrl: string): string {
  if (!isLoopbackBackendUrl(baseUrl)) {
    throw new Error("BrowserHost WebSocket bridge requires a loopback backend baseUrl");
  }
  return buildBackendWebSocketUrl(baseUrl, "/api/ws/browser-host");
}

export function isLoopbackBackendUrl(baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl);
    if (!["http:", "https:"].includes(url.protocol)) return false;
    return isLoopbackHostname(url.hostname);
  } catch {
    return false;
  }
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (normalized === "localhost" || normalized === "::1" || normalized === "0:0:0:0:0:0:0:1") {
    return true;
  }
  const ipv4Match = normalized.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (!ipv4Match) return false;
  return ipv4Match.slice(1).every((part) => Number(part) >= 0 && Number(part) <= 255) && Number(ipv4Match[1]) === 127;
}

export type BrowserHostBridgeMessage =
  | {
      type: "open";
      request_id?: string;
      session_id?: string;
      task_id?: string;
      url?: string;
      title?: string;
      mode?: string;
    }
  | { type: "show" | "pause" | "resume" | "takeover" | "release" | "stop"; request_id?: string; session_id: string }
  | { type: "hide"; request_id?: string }
  | { type: "set_bounds"; request_id?: string; bounds: BrowserHostBounds }
  | { type: "action"; request_id?: string; session_id: string; action: BrowserAction }
  | { type: "ping"; request_id?: string };

export function parseBrowserHostBridgeMessage(rawData: unknown): BrowserHostBridgeMessage | null {
  try {
    const rawText = typeof rawData === "string" ? rawData : rawData instanceof Buffer ? rawData.toString("utf8") : String(rawData);
    const data = JSON.parse(rawText) as Record<string, unknown>;
    const type = typeof data.type === "string" ? data.type : "";
    const request_id = typeof data.request_id === "string" ? data.request_id : undefined;

    if (type === "open") {
      return {
        type,
        request_id,
        session_id: optionalMessageString(data.session_id),
        task_id: optionalMessageString(data.task_id),
        url: optionalMessageString(data.url),
        title: optionalMessageString(data.title),
        mode: optionalMessageString(data.mode)
      };
    }

    if (["show", "pause", "resume", "takeover", "release", "stop"].includes(type)) {
      const session_id = optionalMessageString(data.session_id);
      return session_id ? { type: type as "show" | "pause" | "resume" | "takeover" | "release" | "stop", request_id, session_id } : null;
    }

    if (type === "hide" || type === "ping") {
      return { type, request_id } as BrowserHostBridgeMessage;
    }

    if (type === "set_bounds" && isBounds(data.bounds)) {
      return { type, request_id, bounds: data.bounds };
    }

    if (type === "action" && isBrowserActionMessage(data.action)) {
      const session_id = optionalMessageString(data.session_id);
      return session_id ? { type, request_id, session_id, action: data.action } : null;
    }

    return null;
  } catch {
    return null;
  }
}

function isBounds(value: unknown): value is BrowserHostBounds {
  if (!value || typeof value !== "object") return false;
  const bounds = value as BrowserHostBounds;
  return [bounds.x, bounds.y, bounds.width, bounds.height].every((item) => typeof item === "number" && Number.isFinite(item));
}

function isBrowserActionMessage(value: unknown): value is BrowserAction {
  return Boolean(value && typeof value === "object" && typeof (value as { kind?: unknown }).kind === "string");
}

function optionalMessageString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}
