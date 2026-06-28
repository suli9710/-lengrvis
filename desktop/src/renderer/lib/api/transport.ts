import type {
  AgentConversation,
  ApiMethod,
  ApiRequest,
  ApiQueryValue,
  ApiResponse,
  AppSettings,
  ApprovalDecision,
  ApprovalRequest,
  AuditLogEntry,
  BackendStatus,
  BrowserActivityEvent,
  BrowserAction,
  BrowserHostActionResult,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  BrowserLinkResult,
  BrowserPageSnapshot,
  BrowserSession,
  CommandExecutionResult,
  CommandInfo,
  ChatMessage,
  ChatRequest,
  ChatResponse,
  CleanupExecutionResult,
  CleanupExecuteRequest,
  CleanupItem,
  CleanupPlan,
  CleanupPlanRequest,
  CleanupRollbackRequest,
  CleanupScanRequest,
  ContextUsage,
  DesktopWebSocketSubscribeRequest,
  DiagnosticExportResult,
  DocumentAskRequest,
  DocumentAskResponse,
  DocumentCitation,
  DocumentCompareRequest,
  DocumentCompareResponse,
  DocumentIR,
  DocumentParseRequest,
  DocumentTable,
  FileSearchResponse,
  FileSearchResult,
  FileRevealResult,
  HardwareAccelerationSmokePayload,
  HardwareAccelerationStatusPayload,
  IndexStatus,
  InstalledApp,
  InstalledSkill,
  IntentSuggestion,
  LocalLibraryItem,
  LocalLibraryResponse,
  LLMCostSummary,
  LLMHealthStatus,
  LLMProfile,
  LocalLLMHealth,
  LocalMetricsSummary,
  LocalModelReadiness,
  LocalModelSetupPlan,
  PerceptionSuggestionLaunchRequest,
  PerceptionSuggestionLaunchResponse,
  Plan,
  SafetyReview,
  SkillImportResult,
  SkillsCatalog,
  StartupItem,
  SystemDiagnostic,
  SystemInfo,
  SystemProcess,
  TaskArtifactsSummary,
  TaskCompletionEvidence,
  TaskEvent,
  TaskBoundaryEvent,
  RunEventPayload,
  TaskExplain,
  TaskExplainChainItem,
  TaskExplainEvidence,
  TaskExplainMessage,
  TaskExplainReview,
  TaskExplainStep
} from "../../../shared/types";
import type { DesktopMobilePairingCode } from "../../../shared/mobilePairingPayload";
import {
  API_REQUEST_DENIED_EXACT_PATHS,
  API_REQUEST_DENIED_METHOD_PATHS,
  API_REQUEST_DENIED_PATH_PREFIXES
} from "../../../shared/ipc";
import {
  zhApprovalType,
  zhBackendTaskStatus,
  zhBackendText,
  zhRiskLevel,
  zhSafetyVerdict,
  zhToolName,
  zhUserFacingError
} from "../zh";


export const FALLBACK_BACKEND_URL = "http://127.0.0.1:8000";
export const DEFAULT_TIMEOUT_MS = 30_000;
export const WS_RETRY_DELAY_MS = 2_500;
export const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";
export const WEB_ONLY_DEV_MUTATING_METHODS = new Set<ApiMethod>(["POST", "PUT", "PATCH", "DELETE"]);
export const API_REQUEST_DENIED_EXACT_PATH_SET = new Set<string>(API_REQUEST_DENIED_EXACT_PATHS);

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

export interface LocalModelInstallRequest {
  model?: string;
}

export interface LocalModelInstallResponse {
  ok?: boolean;
  model?: string;
  message?: string;
  error?: string;
  progress?: unknown;
  final?: unknown;
}

export interface OllamaActionResponse {
  ok?: boolean;
  model?: string;
  message?: string;
  error?: string;
  source?: string;
  executable?: string;
  models_dir?: string;
}

export const rendererBatchControllers = new Map<string, AbortController>();

export function mergeRendererAbortSignals(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort();
      return controller.signal;
    }
    signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  return controller.signal;
}

export function resolveRendererBatchSignal(abortGroup: string | undefined): AbortSignal | undefined {
  if (!abortGroup) {
    return undefined;
  }
  let controller = rendererBatchControllers.get(abortGroup);
  if (!controller || controller.signal.aborted) {
    controller = new AbortController();
    rendererBatchControllers.set(abortGroup, controller);
  }
  return controller.signal;
}

export async function requestBackendDirect<TResponse, TBody = unknown>(
  baseUrl: string,
  request: ApiRequest<TBody>
): Promise<ApiResponse<TResponse>> {
  const receivedAt = new Date().toISOString();
  const method = request.method ?? "GET";

  if (isWebOnlyDevBackendBridge()) {
    const guardError = validateWebOnlyDevBackendRequest(request.endpoint, method);
    if (guardError) {
      return {
        ok: false,
        status: 0,
        error: {
          code: "WEB_ONLY_DEV_API_BLOCKED",
          message: guardError
        },
        receivedAt
      };
    }
  }

  try {
    const url = buildRequestUrl(baseUrl, request);
    const timeoutController = new AbortController();
    const timeout = window.setTimeout(() => timeoutController.abort(), request.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    const batchSignal = resolveRendererBatchSignal(request.abortGroup);
    const signal = batchSignal
      ? mergeRendererAbortSignals([batchSignal, timeoutController.signal])
      : timeoutController.signal;
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...(request.body ? { "Content-Type": "application/json" } : {})
    };
    const desktopToken = resolveWebOnlyDevDesktopApiToken();
    if (desktopToken) {
      headers[DESKTOP_API_TOKEN_HEADER] = desktopToken;
    }
    const response = await fetch(url, {
      method,
      headers,
      body: request.body ? JSON.stringify(request.body) : undefined,
      signal
    });
    window.clearTimeout(timeout);

    const data = await parseResponseBody(response);
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: {
          code: `HTTP_${response.status}`,
          message: zhUserFacingError(getErrorMessage(data, response.statusText || `HTTP ${response.status}`)),
          details: data
        },
        receivedAt
      };
    }

    return { ok: true, status: response.status, data: data as TResponse, receivedAt };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: {
        code: "NETWORK_ERROR",
        message: zhUserFacingError(error instanceof Error ? error.message : "Backend request failed")
      },
      receivedAt
    };
  }
}

export function buildRequestUrl(baseUrl: string, request: ApiRequest): URL {
  const url = buildRendererLoopbackBackendApiUrl(baseUrl, request.endpoint, request.query);
  if (!url) {
    throw new Error("Renderer direct backend requests require a loopback HTTP(S) backend");
  }
  return new URL(url);
}

export function emitRendererApiRequestEvent<TBody>(request: ApiRequest<TBody>): void {
  try {
    window.dispatchEvent(
      new CustomEvent("lengrvis-api-request", {
        detail: {
          endpoint: request.endpoint,
          method: request.method ?? "GET"
        }
      })
    );
  } catch {
    // Diagnostics-only event for renderer smoke instrumentation; never block the API call.
  }
}

export function getBackendBaseUrl(baseUrl?: string): string {
  return normalizeRendererLoopbackBackendBaseUrl(baseUrl ?? window.lengrvis?.backendBaseUrl) ?? FALLBACK_BACKEND_URL;
}

export function normalizeRendererLoopbackBackendBaseUrl(baseUrl?: string): string | null {
  const candidate = typeof baseUrl === "string" && baseUrl.trim() ? baseUrl.trim() : FALLBACK_BACKEND_URL;
  try {
    const url = new URL(candidate);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    if (!isRendererLoopbackHostname(url.hostname)) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function buildRendererLoopbackBackendApiUrl(
  baseUrl: string | undefined,
  endpoint: string,
  query?: Record<string, ApiQueryValue>
): string | null {
  const backendBaseUrl = normalizeRendererLoopbackBackendBaseUrl(baseUrl);
  if (!backendBaseUrl) return null;

  const safeEndpoint = validateRendererBackendRelativeEndpoint(endpoint, ["/api"]);
  const url = new URL(safeEndpoint, backendBaseUrl);
  if (url.origin !== new URL(backendBaseUrl).origin) return null;
  appendRendererBackendQuery(url, query);
  return url.toString();
}

export function absoluteRendererLoopbackBackendUrl(pathOrUrl: string, baseUrl?: string): string {
  if (!pathOrUrl) return "";
  const backendBaseUrl = normalizeRendererLoopbackBackendBaseUrl(baseUrl);
  if (!backendBaseUrl) return "";
  try {
    const url = new URL(pathOrUrl, backendBaseUrl);
    if (url.origin !== new URL(backendBaseUrl).origin) return "";
    if (!["http:", "https:"].includes(url.protocol)) return "";
    if (!isRendererLoopbackHostname(url.hostname)) return "";
    return url.toString();
  } catch {
    return "";
  }
}

export function buildRendererLoopbackBackendWebSocketUrl(
  baseUrl: string | undefined,
  endpoint: string,
  query?: Record<string, ApiQueryValue>
): string | null {
  const backendBaseUrl = normalizeRendererLoopbackBackendBaseUrl(baseUrl);
  if (!backendBaseUrl) return null;

  const safeEndpoint = validateRendererBackendRelativeEndpoint(endpoint, ["/ws", "/api/ws"]);
  const url = new URL(safeEndpoint, backendBaseUrl);
  if (url.origin !== new URL(backendBaseUrl).origin) return null;
  appendRendererBackendQuery(url, query);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function validateRendererBackendRelativeEndpoint(endpoint: string, allowedRoots: readonly string[]): string {
  if (typeof endpoint !== "string") {
    throw new Error("Renderer backend endpoint is required");
  }
  if (!endpoint || endpoint.length > 512) {
    throw new Error("Renderer backend endpoint length is invalid");
  }
  if (endpoint.trim() !== endpoint || /\s|[\u0000-\u001F\u007F]/.test(endpoint)) {
    throw new Error("Renderer backend endpoint contains unsafe characters");
  }
  if (endpoint.includes("?") || endpoint.includes("#")) {
    throw new Error("Renderer backend endpoint must not include query strings or fragments");
  }
  if (
    !endpoint.startsWith("/") ||
    endpoint.startsWith("//") ||
    endpoint.includes("//") ||
    endpoint.includes("\\") ||
    /^[a-z][a-z0-9+.-]*:/i.test(endpoint)
  ) {
    throw new Error("Renderer backend endpoints must be backend-relative");
  }
  if (/%2f|%5c/i.test(endpoint)) {
    throw new Error("Renderer backend endpoint must not contain encoded path separators");
  }

  let decodedPath = "";
  try {
    decodedPath = decodeURIComponent(endpoint);
  } catch {
    throw new Error("Renderer backend endpoint encoding is invalid");
  }
  if (decodedPath.includes("\\") || decodedPath.includes("//")) {
    throw new Error("Renderer backend endpoint contains unsafe path separators");
  }
  if (decodedPath.split("/").some((segment) => segment === "." || segment === "..")) {
    throw new Error("Renderer backend endpoint contains unsafe path segments");
  }
  if (!allowedRoots.some((root) => decodedPath === root || decodedPath.startsWith(`${root}/`))) {
    throw new Error("Renderer backend endpoint targets an unsupported backend path");
  }
  return endpoint;
}

export function appendRendererBackendQuery(url: URL, query?: Record<string, ApiQueryValue>): void {
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === null || value === undefined) continue;
    if (!isSafeRendererBackendQueryKey(key)) {
      throw new Error("Renderer backend query key is unsafe");
    }
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new Error("Renderer backend query value must be finite");
    }
    if (!["string", "number", "boolean"].includes(typeof value)) {
      throw new Error("Renderer backend query values must be primitive");
    }
    url.searchParams.set(key, String(value));
  }
}

export function isSafeRendererBackendQueryKey(key: string): boolean {
  return !["__proto__", "constructor", "prototype"].includes(key) && /^[A-Za-z0-9_.:-]{1,96}$/.test(key);
}

export function isRendererLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  return normalized === "localhost" || normalized === "::1" || normalized === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(normalized);
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

export function isWebOnlyDevBackendBridge(): boolean {
  return !window.lengrvis && import.meta.env.DEV;
}

// dev:web only: VITE_LENGRVIS_DEV_SKIP_CONSENT_GATE bypasses ConsentGate when the
// Electron consent bridge is unavailable. Never set this for production builds;
// vite.config.ts strips the value when mode=production.
export function isWebOnlyDevConsentGateBypassEnabled(): boolean {
  if (!isWebOnlyDevBackendBridge()) {
    return false;
  }
  const flag = String(import.meta.env.VITE_LENGRVIS_DEV_SKIP_CONSENT_GATE ?? "").trim().toLowerCase();
  return flag === "true" || flag === "1";
}

// dev:web only: VITE_LENGRVIS_DESKTOP_API_TOKEN bypasses the Electron IPC bridge so
// browser fetches/WebSockets can reach loopback :8000. Never set this for production
// builds; vite.config.ts strips the value when mode=production. Treat the token like
// desktop_api.secret and keep it off shared machines or committed .env files.
export const WEB_ONLY_DEV_DESKTOP_WS_PROTOCOL_PREFIX = "lengrvis.desktop.token.";
export const WEB_ONLY_DEV_WS_PROTOCOL_TOKEN_REGEX = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;

export function resolveWebOnlyDevDesktopApiToken(): string {
  if (!isWebOnlyDevBackendBridge()) {
    return "";
  }
  return String(import.meta.env.VITE_LENGRVIS_DESKTOP_API_TOKEN ?? "").trim();
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

export function validateWebOnlyDevBackendRequest(endpoint: string, method: ApiMethod): string | null {
  const normalizedPath = normalizeRendererApiPath(endpoint);
  if (isDeniedRendererApiPath(normalizedPath, method)) {
    return "该接口需要 Electron 桌面桥接，dev:web 模式不可用。";
  }
  if (WEB_ONLY_DEV_MUTATING_METHODS.has(method) && !resolveWebOnlyDevDesktopApiToken()) {
    return "dev:web 写操作需要配置 VITE_LENGRVIS_DESKTOP_API_TOKEN（与后端 desktop_api.secret 一致）。";
  }
  return null;
}

export function normalizeRendererApiPath(endpoint: string): string {
  const safeEndpoint = validateRendererBackendRelativeEndpoint(endpoint, ["/api"]);
  const segments = decodeURIComponent(safeEndpoint).split("/").filter(Boolean);
  return `/${segments.join("/")}`;
}

export function isDeniedRendererApiPath(pathname: string, method: ApiMethod): boolean {
  if (API_REQUEST_DENIED_EXACT_PATH_SET.has(pathname)) {
    return true;
  }
  if (
    API_REQUEST_DENIED_PATH_PREFIXES.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
    )
  ) {
    return true;
  }
  return API_REQUEST_DENIED_METHOD_PATHS.some((rule) => {
    if (rule.method !== method) {
      return false;
    }
    if ("path" in rule) {
      return pathname === rule.path;
    }
    if (!pathname.startsWith(rule.pathPrefix)) {
      return false;
    }
    if ("pathSuffix" in rule) {
      return pathname.endsWith(rule.pathSuffix);
    }
    return true;
  });
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
  } catch (error) {
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

export async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (response.status === 204) return undefined;
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  return text ? { message: text } : undefined;
}

export function getErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object") {
    const direct = (data as { message?: unknown }).message;
    if (typeof direct === "string") return direct;
    const nested = (data as { error?: { message?: unknown } }).error?.message;
    if (typeof nested === "string") return nested;
  }
  return fallback || "Backend request failed";
}

export function mapResponse<TInput, TOutput>(
  response: ApiResponse<TInput>,
  mapper: (data: TInput) => TOutput
): ApiResponse<TOutput> {
  if (!response.ok || response.data === undefined) {
    return {
      ok: response.ok,
      status: response.status,
      error: response.error,
      receivedAt: response.receivedAt
    };
  }
  return {
    ok: true,
    status: response.status,
    data: mapper(response.data),
    receivedAt: response.receivedAt
  };
}

