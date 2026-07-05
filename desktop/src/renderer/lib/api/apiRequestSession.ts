import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import {
  FALLBACK_BACKEND_URL,
  emitRendererApiRequestEvent,
  rendererBatchControllers,
  requestBackendDirect
} from "./transport";

export class RendererApiRequestSession {
  private activeAbortGroup: string | null = null;
  private activeBatchStack: string[] = [];

  async abortInflight(abortGroup: string): Promise<void> {
    rendererBatchControllers.get(abortGroup)?.abort();
    rendererBatchControllers.delete(abortGroup);
    await window.lengrvis?.api.abortInflight(abortGroup);
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
    const abortGroup = request.abortGroup ?? this.activeAbortGroup ?? undefined;
    const enrichedRequest = abortGroup ? { ...request, abortGroup } : request;
    emitRendererApiRequestEvent(enrichedRequest);
    if (!window.lengrvis) {
      return requestBackendDirect<TResponse, TBody>(FALLBACK_BACKEND_URL, enrichedRequest);
    }

    return window.lengrvis.api.request<TResponse, TBody>(enrichedRequest);
  }
}
