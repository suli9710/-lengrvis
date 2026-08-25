import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import { safeIpcApiRequest } from "./apiRequestSession";
import type { BackendMemory } from "./memoryBackendTypes";

export interface SaveMemoryOptions {
  tags?: string[];
  taskId?: string;
  kind?: string;
}

export interface RecallMemoryOptions {
  k?: number;
  tags?: string[];
}

export interface ReviewMemoryOptions {
  reviewedBy?: string;
  resolveConflict?: boolean;
}

export type MemoryEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function listMemoriesEndpoint(
  request: MemoryEndpointRequest
): Promise<ApiResponse<BackendMemory[]>> {
  return request<BackendMemory[]>({ endpoint: "/api/memories" });
}

export function saveMemoryEndpoint(
  request: MemoryEndpointRequest,
  content: string,
  options: SaveMemoryOptions = {}
): Promise<ApiResponse<BackendMemory>> {
  const input = {
    content,
    tags: options.tags ?? [],
    taskId: options.taskId ?? "",
    kind: options.kind ?? "fact"
  };
  if (window.lengrvis?.memories) {
    return safeIpcApiRequest(() =>
      window.lengrvis.memories.save(input) as Promise<ApiResponse<BackendMemory>>
    );
  }
  return request<BackendMemory, { content: string; tags: string[]; task_id: string; kind: string }>({
    endpoint: "/api/memories",
    method: "POST",
    body: {
      content: input.content,
      tags: input.tags,
      task_id: input.taskId,
      kind: input.kind
    }
  });
}

export function recallMemoryEndpoint(
  request: MemoryEndpointRequest,
  query: string,
  options: RecallMemoryOptions = {}
): Promise<ApiResponse<BackendMemory[]>> {
  const input = {
    query,
    k: options.k ?? 5,
    tags: options.tags ?? []
  };
  if (window.lengrvis?.memories) {
    return safeIpcApiRequest(() =>
      window.lengrvis.memories.recall(input) as Promise<ApiResponse<BackendMemory[]>>
    );
  }
  return request<BackendMemory[], typeof input>({
    endpoint: "/api/memories/recall",
    method: "POST",
    body: input
  });
}

export function promoteMemoryEndpoint(
  request: MemoryEndpointRequest,
  memoryId: string,
  options: ReviewMemoryOptions = {}
): Promise<ApiResponse<BackendMemory>> {
  const input = {
    memoryId,
    reviewedBy: options.reviewedBy ?? "desktop-user",
    resolveConflict: options.resolveConflict ?? false
  };
  if (window.lengrvis?.memories) {
    return safeIpcApiRequest(() =>
      window.lengrvis.memories.promote(input) as Promise<ApiResponse<BackendMemory>>
    );
  }
  return request<BackendMemory, { reviewed_by: string; conflict_status?: "resolved" }>({
    endpoint: `/api/memories/${encodeURIComponent(memoryId)}/promote`,
    method: "POST",
    body: {
      reviewed_by: input.reviewedBy,
      ...(input.resolveConflict ? { conflict_status: "resolved" as const } : {})
    }
  });
}

export function revokeMemoryEndpoint(
  request: MemoryEndpointRequest,
  memoryId: string,
  options: ReviewMemoryOptions = {}
): Promise<ApiResponse<BackendMemory>> {
  const input = {
    memoryId,
    reviewedBy: options.reviewedBy ?? "desktop-user"
  };
  if (window.lengrvis?.memories) {
    return safeIpcApiRequest(() =>
      window.lengrvis.memories.revoke(input) as Promise<ApiResponse<BackendMemory>>
    );
  }
  return request<BackendMemory, { reviewed_by: string }>({
    endpoint: `/api/memories/${encodeURIComponent(memoryId)}/revoke`,
    method: "POST",
    body: { reviewed_by: input.reviewedBy }
  });
}

export function forgetMemoryEndpoint(
  request: MemoryEndpointRequest,
  memoryId: string
): Promise<ApiResponse<{ ok: boolean; id: string }>> {
  if (window.lengrvis?.memories) {
    return safeIpcApiRequest(() =>
      window.lengrvis.memories.forget(memoryId) as Promise<ApiResponse<{ ok: boolean; id: string }>>
    );
  }
  return request({
    endpoint: `/api/memories/${memoryId}`,
    method: "DELETE"
  });
}
