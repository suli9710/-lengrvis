import type { ApiRequest, ApiResponse, BackendStatus } from "../../../shared/desktopBridgeTypes";
import { FALLBACK_BACKEND_URL, getBackendBaseUrl, requestBackendDirect } from "./transport";

type RequestEndpoint = <TResponse, TBody = unknown>(request: ApiRequest<TBody>) => Promise<ApiResponse<TResponse>>;

export async function getBackendStatusEndpoint(requestEndpoint: RequestEndpoint): Promise<BackendStatus> {
  if (window.lengrvis) {
    return window.lengrvis.backend.getStatus();
  }

  const startedAt = Date.now();
  const health = await requestEndpoint<{ status: string }>({ endpoint: "/api/health", timeoutMs: 1500 });
  return {
    state: health.ok ? "running" : "stopped",
    baseUrl: FALLBACK_BACKEND_URL,
    message: health.ok ? "后端已连接" : "等待后端连接",
    lastCheckedAt: new Date().toISOString(),
    health: {
      ok: health.ok,
      latencyMs: Date.now() - startedAt
    }
  };
}

export async function probeBackendHealthEndpoint(baseUrl?: string): Promise<BackendStatus | null> {
  const startedAt = Date.now();
  const backendBaseUrl = getBackendBaseUrl(baseUrl);
  const health = await requestBackendDirect<{ status?: string }>(backendBaseUrl, {
    endpoint: "/api/health",
    timeoutMs: 1500
  });
  if (!health.ok) return null;
  return {
    state: "running",
    baseUrl: backendBaseUrl,
    message: "后端已响应任务请求",
    lastCheckedAt: new Date().toISOString(),
    health: {
      ok: true,
      latencyMs: Date.now() - startedAt
    }
  };
}

export function startBackendEndpoint(getBackendStatus: () => Promise<BackendStatus>): Promise<BackendStatus> {
  if (!window.lengrvis) {
    return getBackendStatus();
  }
  return window.lengrvis.backend.start();
}

export function stopBackendEndpoint(getBackendStatus: () => Promise<BackendStatus>): Promise<BackendStatus> {
  if (!window.lengrvis) {
    return getBackendStatus();
  }
  return window.lengrvis.backend.stop();
}
