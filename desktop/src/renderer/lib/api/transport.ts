import type {
  ApiMethod,
  ApiQueryValue,
  ApiRequest,
  ApiResponse
} from "../../../shared/desktopBridgeTypes";
import { backendErrorMessage } from "../../../shared/backendError";
import {
  API_REQUEST_DENIED_EXACT_PATHS,
  API_REQUEST_DENIED_METHOD_PATHS,
  API_REQUEST_DENIED_PATH_PREFIXES
} from "../../../shared/ipc";
import { zhUserFacingError } from "../zh";


export const FALLBACK_BACKEND_URL = "http://127.0.0.1:8000";
export const DEFAULT_TIMEOUT_MS = 30_000;
export const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";
export const WEB_ONLY_DEV_MUTATING_METHODS = new Set<ApiMethod>(["POST", "PUT", "PATCH", "DELETE"]);
export const API_REQUEST_DENIED_EXACT_PATH_SET = new Set<string>(API_REQUEST_DENIED_EXACT_PATHS);

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
  } catch (error) { // broad-exception-boundary
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
export function resolveWebOnlyDevDesktopApiToken(): string {
  if (!isWebOnlyDevBackendBridge()) {
    return "";
  }
  return String(import.meta.env.VITE_LENGRVIS_DESKTOP_API_TOKEN ?? "").trim();
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

export async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (response.status === 204) return undefined;
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  return text ? { message: text } : undefined;
}

export function getErrorMessage(data: unknown, fallback: string): string {
  return backendErrorMessage(data, fallback || "Backend request failed");
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

