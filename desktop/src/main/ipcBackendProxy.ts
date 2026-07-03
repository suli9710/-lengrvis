import type { ApiRequest, ApiResponse, BackendStatus } from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import { proxyApiRequest, type InternalDesktopBridgeRequest } from "./ipcApiProxy";
import { isPlainRecord } from "./ipcValidation";

export function proxyExplicitDesktopBridgeRequest<TData>(
  backend: BackendProcessManager,
  request: InternalDesktopBridgeRequest
): Promise<ApiResponse<TData>> {
  return proxyApiRequest(backend.getBaseUrl(), request, backend.getDesktopApiToken(), {
    allowDeniedDesktopBridgePath: true,
    allowInternalHeaders: true
  });
}

export function proxyRendererApiRequest<TData>(
  backend: BackendProcessManager,
  request: ApiRequest
): Promise<ApiResponse<TData>> {
  return proxyApiRequest(backend.getBaseUrl(), request, backend.getDesktopApiToken());
}

export async function ensureBackendReadyForRendererSubmission(
  backend: BackendProcessManager
): Promise<ApiResponse<never> | null> {
  const receivedAt = new Date().toISOString();
  try {
    const status = await backend.enterForeground("renderer_task_submit");
    if (status.health?.ok || (status.state === "running" && !status.health)) {
      return null;
    }
    return backendNotReadyResponse(status, receivedAt);
  } catch (error) { // broad-exception-boundary
    return {
      ok: false,
      status: 503,
      error: {
        code: "BACKEND_NOT_READY",
        message: error instanceof Error ? error.message : "Backend is not ready for task submission"
      },
      receivedAt
    };
  }
}

export function isRendererTaskSubmissionRequest(request: unknown): boolean {
  if (!isPlainRecord(request) || typeof request.endpoint !== "string") {
    return false;
  }
  const method = typeof request.method === "string" ? request.method.toUpperCase() : "GET";
  if (method !== "POST") {
    return false;
  }
  return (
    request.endpoint === "/api/chat" ||
    (request.endpoint.startsWith("/api/perception/suggestions/") && request.endpoint.endsWith("/launch"))
  );
}

function backendNotReadyResponse(status: BackendStatus, receivedAt: string): ApiResponse<never> {
  return {
    ok: false,
    status: 503,
    error: {
      code: "BACKEND_NOT_READY",
      message: status.message
        ? `Backend is not ready for task submission: ${status.message}`
        : "Backend is not ready for task submission",
      details: { backendStatus: status }
    },
    receivedAt
  };
}
