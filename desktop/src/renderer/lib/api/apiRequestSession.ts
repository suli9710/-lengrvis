import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import {
  FALLBACK_BACKEND_URL,
  emitRendererApiRequestEvent,
  rendererBatchControllers,
  requestBackendDirect
} from "./transport";

export function ipcRequestFailedResponse<TData>(): ApiResponse<TData> {
  return {
    ok: false,
    status: 0,
    error: {
      code: "IPC_REQUEST_FAILED",
      message: "Lengrvis 桌面连接暂时不可用，请重启应用后再试。"
    },
    receivedAt: new Date().toISOString()
  };
}

export async function safeIpcApiRequest<TData>(
  invoke: () => Promise<ApiResponse<TData>>
): Promise<ApiResponse<TData>> {
  try {
    return await invoke();
  } catch { // broad-exception-boundary: preserve the sanitized ApiResponse contract for specialized IPC bridges.
    return ipcRequestFailedResponse<TData>();
  }
}

export class RendererApiRequestSession {
  private activeAbortGroup: string | null = null;
  private activeBatchStack: string[] = [];

  async abortInflight(abortGroup: string): Promise<void> {
    rendererBatchControllers.get(abortGroup)?.abort();
    rendererBatchControllers.delete(abortGroup);
    try {
      await window.lengrvis?.api.abortInflight(abortGroup);
    } catch { // broad-exception-boundary: cleanup must remain best-effort during renderer shutdown.
      // The local AbortController is already cancelled; a closed Electron
      // bridge must not prevent a new batch from starting or leak a rejection.
    }
  }

  async beginBatch(abortGroup: string): Promise<void> {
    await this.abortInflight(abortGroup);
    this.activeBatchStack.push(abortGroup);
    this.activeAbortGroup = abortGroup;
    rendererBatchControllers.set(abortGroup, new AbortController());
  }

  endBatch(abortGroup: string): void {
    const index = this.activeBatchStack.lastIndexOf(abortGroup);
    if (index >= 0) {
      this.activeBatchStack.splice(index, 1);
    }
    this.activeAbortGroup = this.activeBatchStack[this.activeBatchStack.length - 1] ?? null;
  }

  async request<TResponse, TBody = unknown>(request: ApiRequest<TBody>): Promise<ApiResponse<TResponse>> {
    // Reads join the active batch so a newer snapshot refresh can cancel stale
    // in-flight reads. Mutations must NOT inherit the batch group: otherwise the
    // next snapshot batch's abortInflight() would silently cancel a
    // user-initiated write (approval decision, chat send, …) with no error. A
    // mutation is aborted only when it explicitly opts into an abortGroup.
    const method = (request.method ?? "GET").toUpperCase();
    const isMutation = method !== "GET" && method !== "HEAD";
    const inheritedGroup = isMutation ? undefined : (this.activeAbortGroup ?? undefined);
    const abortGroup = request.abortGroup ?? inheritedGroup;
    const enrichedRequest = abortGroup ? { ...request, abortGroup } : request;
    emitRendererApiRequestEvent(enrichedRequest);
    if (!window.lengrvis) {
      return requestBackendDirect<TResponse, TBody>(FALLBACK_BACKEND_URL, enrichedRequest);
    }

    try {
      return await window.lengrvis.api.request<TResponse, TBody>(enrichedRequest);
    } catch { // broad-exception-boundary: preserve the ApiResponse contract across IPC failures.
      return ipcRequestFailedResponse<TResponse>();
    }
  }
}
