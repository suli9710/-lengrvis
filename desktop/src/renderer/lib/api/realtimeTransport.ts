import type { DesktopWebSocketSubscribeRequest } from "../../../shared/desktopBridgeTypes";
import { zhUserFacingError } from "../zh";
import {
  buildRendererLoopbackBackendWebSocketUrl,
  getBackendBaseUrl,
  isWebOnlyDevBackendBridge,
  resolveWebOnlyDevDesktopApiToken
} from "./transport";

export const WS_RETRY_DELAY_MS = 2_500;
export const WEB_ONLY_DEV_DESKTOP_WS_PROTOCOL_PREFIX = "lengrvis.desktop.token.";
export const WEB_ONLY_DEV_WS_PROTOCOL_TOKEN_REGEX = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;

export type RealtimeConnectionState =
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed"
  | "error"
  | "unauthorized"
  | "policy_violation"
  | "bad_message";

export interface RealtimeConnectionStatus {
  state: RealtimeConnectionState;
  endpoint: string;
  at: string;
  attempt?: number;
  code?: number;
  reason?: string;
  wasClean?: boolean;
  retryInMs?: number;
  message?: string;
  rawMessage?: string;
}

export interface JsonRealtimeHandlers<TMessage> {
  onMessage: (message: TMessage) => void;
  onError?: (error: Event) => void;
  onOpen?: () => void;
  onStatus?: (status: RealtimeConnectionStatus) => void;
  onBadMessage?: (status: RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string }) => void;
}

export function subscribeJsonRealtime<TMessage>(
  request: DesktopWebSocketSubscribeRequest,
  handlers: JsonRealtimeHandlers<TMessage>
): () => void {
  if (window.lengrvis?.realtime) {
    return subscribeDesktopJsonStream(request, handlers);
  }

  if (typeof WebSocket === "undefined") {
    return () => undefined;
  }

  if (!isWebOnlyDevRealtimeFallbackEnabled()) {
    emitRealtimeStatus(request, handlers, "error", {
      message: "Desktop realtime bridge is unavailable"
    });
    return () => undefined;
  }

  const url = buildRendererLoopbackBackendWebSocketUrl(getBackendBaseUrl(), request.endpoint, request.query);
  if (!url) {
    emitRealtimeStatus(request, handlers, "error", {
      message: "Renderer web-only realtime fallback requires a loopback backend"
    });
    return () => undefined;
  }

  const devTokenError = validateWebOnlyDevRealtimeToken();
  if (devTokenError) {
    emitRealtimeStatus(request, handlers, "error", { message: devTokenError });
    return () => undefined;
  }

  return subscribeWebOnlyDevJsonStream(url, request, handlers);
}

export function isWebOnlyDevRealtimeFallbackEnabled(): boolean {
  return isWebOnlyDevBackendBridge();
}

export function validateWebOnlyDevRealtimeToken(): string | null {
  if (!isWebOnlyDevBackendBridge()) {
    return null;
  }
  const token = resolveWebOnlyDevDesktopApiToken();
  if (!token) {
    return "dev:web WebSocket 需要配置 VITE_LENGRVIS_DESKTOP_API_TOKEN（与后端 desktop_api.secret 一致）。";
  }
  const protocol = `${WEB_ONLY_DEV_DESKTOP_WS_PROTOCOL_PREFIX}${token}`;
  if (!WEB_ONLY_DEV_WS_PROTOCOL_TOKEN_REGEX.test(protocol)) {
    return "dev:web WebSocket token 不能作为 WebSocket subprotocol 使用。";
  }
  return null;
}

export function webOnlyDevDesktopWebSocketProtocols(token: string): [string] {
  const protocol = `${WEB_ONLY_DEV_DESKTOP_WS_PROTOCOL_PREFIX}${token.trim()}`;
  return [protocol];
}

export function subscribeDesktopJsonStream<TMessage>(
  request: DesktopWebSocketSubscribeRequest,
  handlers: JsonRealtimeHandlers<TMessage>
): () => void {
  let unsubscribeSocket: (() => void) | null = null;
  let closedByCaller = false;
  let retryId: number | undefined;
  let reconnectAttempt = 0;

  const connect = () => {
    const state = reconnectAttempt > 0 ? "reconnecting" : "connecting";
    emitRealtimeStatus(request, handlers, state, {
      attempt: reconnectAttempt,
      retryInMs: reconnectAttempt > 0 ? WS_RETRY_DELAY_MS : undefined
    });

    unsubscribeSocket = window.lengrvis!.realtime.subscribe(request, {
      onOpen: () => {
        reconnectAttempt = 0;
        emitRealtimeStatus(request, handlers, "open");
        handlers.onOpen?.();
      },
      onMessage: (data) => {
        parseJsonRealtimeMessage(data, request, handlers);
      },
      onError: (error) => {
        const status = realtimeStatusFromError(request, error);
        handlers.onStatus?.(status);
        handlers.onError?.(makeWebSocketErrorEvent(status.message));
      },
      onClose: (event) => {
        unsubscribeSocket = null;
        if (closedByCaller) return;
        const status = realtimeStatusFromClose(request, event, reconnectAttempt + 1);
        if (shouldRetryRealtime(status)) {
          reconnectAttempt += 1;
          handlers.onStatus?.({
            ...status,
            state: "reconnecting",
            attempt: reconnectAttempt,
            retryInMs: WS_RETRY_DELAY_MS
          });
          retryId = window.setTimeout(connect, WS_RETRY_DELAY_MS);
          return;
        }
        handlers.onStatus?.(status);
      }
    });
  };

  connect();

  return () => {
    closedByCaller = true;
    if (retryId !== undefined) window.clearTimeout(retryId);
    unsubscribeSocket?.();
    unsubscribeSocket = null;
  };
}

export function subscribeWebOnlyDevJsonStream<TMessage>(
  url: string,
  request: DesktopWebSocketSubscribeRequest,
  handlers: JsonRealtimeHandlers<TMessage>
): () => void {
  let socket: WebSocket | null = null;
  let closedByCaller = false;
  let retryId: number | undefined;
  let reconnectAttempt = 0;

  const connect = () => {
    const state = reconnectAttempt > 0 ? "reconnecting" : "connecting";
    emitRealtimeStatus(request, handlers, state, {
      attempt: reconnectAttempt,
      retryInMs: reconnectAttempt > 0 ? WS_RETRY_DELAY_MS : undefined
    });

    const devToken = resolveWebOnlyDevDesktopApiToken();
    socket = new WebSocket(url, webOnlyDevDesktopWebSocketProtocols(devToken));

    socket.onopen = () => {
      reconnectAttempt = 0;
      emitRealtimeStatus(request, handlers, "open");
      handlers.onOpen?.();
    };
    socket.onmessage = (event) => {
      parseJsonRealtimeMessage(event.data, request, handlers);
    };
    socket.onerror = (event) => {
      const status = realtimeStatusFromError(request, event);
      handlers.onStatus?.(status);
      handlers.onError?.(event);
    };
    socket.onclose = (event) => {
      socket = null;
      if (closedByCaller) return;
      const status = realtimeStatusFromClose(request, event, reconnectAttempt + 1);
      if (shouldRetryRealtime(status)) {
        reconnectAttempt += 1;
        handlers.onStatus?.({
          ...status,
          state: "reconnecting",
          attempt: reconnectAttempt,
          retryInMs: WS_RETRY_DELAY_MS
        });
        retryId = window.setTimeout(connect, WS_RETRY_DELAY_MS);
        return;
      }
      handlers.onStatus?.(status);
    };
  };

  connect();

  return () => {
    closedByCaller = true;
    if (retryId !== undefined) window.clearTimeout(retryId);
    socket?.close();
    socket = null;
  };
}

export function parseJsonRealtimeMessage<TMessage>(
  data: unknown,
  request: DesktopWebSocketSubscribeRequest,
  handlers: JsonRealtimeHandlers<TMessage>
): void {
  const rawMessage = rawRealtimeMessage(data);
  try {
    handlers.onMessage(JSON.parse(rawMessage) as TMessage);
  } catch (error) { // broad-exception-boundary
    const status = createRealtimeStatus(request, "bad_message", {
      message: error instanceof Error ? error.message : "Malformed realtime message",
      rawMessage
    });
    handlers.onBadMessage?.(status as RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string });
    handlers.onStatus?.(status);
  }
}

export function emitRealtimeStatus<TMessage>(
  request: DesktopWebSocketSubscribeRequest,
  handlers: Pick<JsonRealtimeHandlers<TMessage>, "onStatus">,
  state: RealtimeConnectionState,
  patch: Partial<RealtimeConnectionStatus> = {}
): void {
  handlers.onStatus?.(createRealtimeStatus(request, state, patch));
}

export function createRealtimeStatus(
  request: DesktopWebSocketSubscribeRequest,
  state: RealtimeConnectionState,
  patch: Partial<RealtimeConnectionStatus> = {}
): RealtimeConnectionStatus {
  return {
    state,
    endpoint: request.endpoint,
    at: new Date().toISOString(),
    ...patch
  };
}

export function realtimeStatusFromError(
  request: DesktopWebSocketSubscribeRequest,
  error: unknown
): RealtimeConnectionStatus {
  const message = realtimeErrorMessage(error);
  return createRealtimeStatus(request, classifyRealtimeIssue(undefined, message) ?? "error", { message });
}

export function realtimeStatusFromClose(
  request: DesktopWebSocketSubscribeRequest,
  event: { code?: number; reason?: string; wasClean?: boolean },
  attempt: number
): RealtimeConnectionStatus {
  const code = event.code;
  const reason = event.reason ?? "";
  const state = classifyRealtimeIssue(code, reason);
  if (state) {
    return createRealtimeStatus(request, state, {
      code,
      reason,
      wasClean: event.wasClean,
      attempt
    });
  }
  return createRealtimeStatus(request, event.wasClean || code === 1000 ? "closed" : "reconnecting", {
    code,
    reason,
    wasClean: event.wasClean,
    attempt
  });
}

export function classifyRealtimeIssue(code?: number, message = ""): RealtimeConnectionState | null {
  const lower = message.toLowerCase();
  if (
    code === 1008 ||
    lower.includes("1008") ||
    lower.includes("policy violation") ||
    lower.includes("policy_violation")
  ) {
    return "policy_violation";
  }
  if (
    lower.includes("401") ||
    lower.includes("unauthorized") ||
    lower.includes("missing desktop api token")
  ) {
    return "unauthorized";
  }
  return null;
}

export function shouldRetryRealtime(status: RealtimeConnectionStatus): boolean {
  return status.state === "reconnecting" || status.state === "error";
}

export function realtimeErrorMessage(error: unknown): string {
  let message = "";
  if (error && typeof error === "object" && "message" in error && typeof (error as { message?: unknown }).message === "string") {
    message = (error as { message: string }).message;
  } else if (error instanceof Error) {
    message = error.message;
  } else {
    message = "Realtime connection error";
  }
  return zhUserFacingError(message) || "实时连接遇到错误，正在尝试恢复";
}

export function rawRealtimeMessage(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    const serialized = JSON.stringify(value);
    if (serialized) return serialized;
  } catch {
    // Fall back to String below so malformed values are still visible to the UI.
  }
  return String(value);
}

export function buildBrowserSessionWebSocketUrl(baseUrl: string, sessionId: string): string {
  return buildRendererLoopbackBackendWebSocketUrl(baseUrl, `/api/ws/browser/sessions/${encodeURIComponent(sessionId)}`) ?? "";
}

export function makeWebSocketErrorEvent(message?: string): Event {
  return typeof Event === "function"
    ? Object.assign(new Event("error"), { message })
    : ({ type: "error", message } as unknown as Event);
}
