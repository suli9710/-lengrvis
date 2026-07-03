import type { BackendErrorCode, BaseUrlSecurity } from "./types";
import { INSECURE_LAN_HTTP_WARNING } from "./types";

export const DEFAULT_FETCH_TIMEOUT_MS = 30_000;

export class FetchTimeoutError extends Error {
  readonly code: BackendErrorCode = "network";

  constructor(timeoutMs: number = DEFAULT_FETCH_TIMEOUT_MS) {
    super(`请求超时（${Math.round(timeoutMs / 1000)} 秒）。请确认电脑端 Lengrvis 已打开后重试。`);
    this.name = "FetchTimeoutError";
  }
}

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit,
  timeoutMs: number = DEFAULT_FETCH_TIMEOUT_MS,
): Promise<Response> {
  if (typeof AbortController === "undefined") {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeoutPromise = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new FetchTimeoutError(timeoutMs)), timeoutMs);
    });
    try {
      return await Promise.race([fetch(input, init), timeoutPromise]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const upstreamSignal = init?.signal;
  const abortFromUpstream = () => controller.abort();
  if (upstreamSignal) {
    if (upstreamSignal.aborted) {
      clearTimeout(timer);
      controller.abort();
    } else {
      upstreamSignal.addEventListener("abort", abortFromUpstream, { once: true });
    }
  }
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) { // broad-exception-boundary
    if (error instanceof Error && error.name === "AbortError") {
      if (upstreamSignal?.aborted) throw error;
      throw new FetchTimeoutError(timeoutMs);
    }
    throw error;
  } finally {
    clearTimeout(timer);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}


export class AuthExpiredError extends Error {
  readonly status = 401;
  readonly code = "auth_expired";

  constructor(message = "这台手机已断开连接。请在 Lengrvis 中重新连接。") {
    super(message);
    this.name = "AuthExpiredError";
  }
}

export class ForbiddenError extends Error {
  readonly status = 403;
  readonly code = "forbidden";

  constructor(message = "这台手机没有权限执行该操作。") {
    super(message);
    this.name = "ForbiddenError";
  }
}

export class BackendHttpError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code: BackendErrorCode;

  constructor(status: number, detail: string, code: BackendErrorCode = backendErrorCodeForStatus(status)) {
    super(detail || "Lengrvis 未能完成该请求，请重试。");
    this.name = "BackendHttpError";
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

export class InsecureLanBaseUrlError extends Error {
  readonly security: BaseUrlSecurity;

  constructor(security: BaseUrlSecurity, message = security.warning || INSECURE_LAN_HTTP_WARNING) {
    super(message);
    this.name = "InsecureLanBaseUrlError";
    this.security = security;
  }
}

export type RemoteInputGrantJsonContext = "claim" | "use";

export async function parseRemoteInputGrantJson<T>(response: Response, context: RemoteInputGrantJsonContext): Promise<T> {
  const data = await response.json().catch(() => undefined);
  if (!response.ok) {
    throw remoteInputGrantResponseError(response.status, responseDetailMessage(data), context);
  }
  return data as T;
}

export function remoteInputGrantResponseError(status: number, detail: string, context: RemoteInputGrantJsonContext): Error {
  const normalized = detail.toLowerCase();
  if (status === 401) {
    if (normalized.includes("mobile device")) {
      return new AuthExpiredError(detail || undefined);
    }
    if (normalized.includes("remote input grant expired")) {
      return new BackendHttpError(410, detail || "Remote input grant expired.");
    }
    if (normalized.includes("remote input grant")) {
      return new ForbiddenError(detail || undefined);
    }
    if (context === "use" && (normalized.includes("mobile token expired") || normalized.includes("invalid mobile token"))) {
      return new BackendHttpError(410, detail || "Remote input grant token expired.");
    }
    return new AuthExpiredError(detail || undefined);
  }
  if (status === 403) {
    if (normalized.includes("remote input grant")) {
      return new BackendHttpError(410, terminalRemoteInputGrantDetail(detail));
    }
    return new ForbiddenError(detail || undefined);
  }
  return new BackendHttpError(status, detail);
}

export function terminalRemoteInputGrantDetail(detail: string): string {
  const normalized = detail.trim().toLowerCase();
  if (!normalized) return "Remote input grant is not active.";
  if (normalized.includes("not active") || normalized.includes("expired") || normalized.includes("revoked")) return detail;
  return "Remote input grant is not active.";
}

export function backendErrorCodeForStatus(status: number): BackendErrorCode {
  if (status === 400 || status === 422) return "validation";
  if (status === 401) return "auth_expired";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 410) return "gone";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server_error";
  if (status <= 0) return "network";
  return "unknown";
}

export function invalidPairingResponse(detail: string): BackendHttpError {
  return new BackendHttpError(400, detail, "invalid_pairing_response");
}

export async function parseJson<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = responseDetailMessage(data);
    if (response.status === 401) {
      throw new AuthExpiredError(detail || undefined);
    }
    if (response.status === 403) {
      throw new ForbiddenError(detail || undefined);
    }
    throw new BackendHttpError(response.status, detail);
  }
  return data as T;
}

export function responseDetailMessage(data: unknown): string {
  if (!data || typeof data !== "object" || !("detail" in data)) return "";
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg?: unknown }).msg) : ""))
      .filter(Boolean);
    return messages.join("；");
  }
  if (detail && typeof detail === "object" && "msg" in detail) {
    return String((detail as { msg?: unknown }).msg);
  }
  return detail === undefined || detail === null ? "" : String(detail);
}

export function authHeaders(token: string): Record<string, string> {
  return {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
  };
}

export function jsonAuthHeaders(token: string): Record<string, string> {
  return {
    ...authHeaders(token),
    "Content-Type": "application/json",
  };
}
