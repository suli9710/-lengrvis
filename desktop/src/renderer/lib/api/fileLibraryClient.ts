import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  FileClusterOptions,
  FileRevealResult,
  FileSearchResponse,
  LocalLibraryResponse
} from "../../../shared/fileLibraryTypes";
import { zhUserFacingError } from "../zh";
import type {
  BackendClusterRequest,
  BackendClusterResponse,
  BackendFileRevealResult,
  BackendFileSearchResponse,
  BackendLocalLibraryResponse
} from "./fileLibraryBackendTypes";
import { fileClusterRequestFor, mapFileSearchResponse, mapLocalLibraryResponse } from "./libraryMappers";
import { mapFileRevealResult } from "./mappers";
import { mapResponse } from "./transport";

export type FileLibraryEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function searchFilesEndpoint(
  request: FileLibraryEndpointRequest,
  query: string
): Promise<ApiResponse<FileSearchResponse>> {
  return request<BackendFileSearchResponse>({
    endpoint: "/api/files/search",
    query: { q: query },
    timeoutMs: 10_000
  }).then((response) => mapResponse(response, mapFileSearchResponse));
}

export function listLocalLibraryEndpoint(
  request: FileLibraryEndpointRequest,
  section: string,
  query = "",
  limit = 240
): Promise<ApiResponse<LocalLibraryResponse>> {
  return request<BackendLocalLibraryResponse>({
    endpoint: "/api/library",
    query: { section, q: query, limit },
    timeoutMs: 20_000
  }).then((response) => mapResponse(response, mapLocalLibraryResponse));
}

export function clusterFilesEndpoint(
  request: FileLibraryEndpointRequest,
  options: FileClusterOptions = {}
): Promise<ApiResponse<BackendClusterResponse>> {
  return request<BackendClusterResponse, BackendClusterRequest>({
    endpoint: "/api/files/cluster",
    method: "POST",
    body: fileClusterRequestFor(options),
    timeoutMs: 15_000
  });
}

function revealFileViaBackend(
  request: FileLibraryEndpointRequest,
  path: string
): Promise<ApiResponse<FileRevealResult>> {
  return request<BackendFileRevealResult, { path: string }>({
    endpoint: "/api/apps/reveal",
    method: "POST",
    body: { path },
    timeoutMs: 10_000
  }).then((response) => mapResponse(response, mapFileRevealResult));
}

export function revealFileEndpoint(
  request: FileLibraryEndpointRequest,
  path: string
): Promise<ApiResponse<FileRevealResult>> {
  if (!window.lengrvis?.shell.showItemInFolder) {
    return revealFileViaBackend(request, path);
  }
  return showItemInFolderEndpoint(request, path);
}

export async function showItemInFolderEndpoint(
  request: FileLibraryEndpointRequest,
  path: string
): Promise<ApiResponse<FileRevealResult>> {
  if (!window.lengrvis?.shell.showItemInFolder) {
    return revealFileViaBackend(request, path);
  }
  const receivedAt = new Date().toISOString();
  try {
    const result = await window.lengrvis.shell.showItemInFolder(path);
    return {
      ok: result.ok,
      status: result.ok ? 200 : 400,
      data: result,
      error: result.ok ? undefined : { message: result.error ?? "无法打开所在位置" },
      receivedAt
    };
  } catch (error) { // broad-exception-boundary
    return {
      ok: false,
      status: 0,
      error: { message: zhUserFacingError(error instanceof Error ? error.message : "无法打开所在位置") },
      receivedAt
    };
  }
}
