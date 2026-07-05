import type {
  FileClusterOptions,
  FileSearchResponse,
  FileSearchResult,
  IndexStatus,
  LocalLibraryItem,
  LocalLibraryResponse
} from "../../../shared/fileLibraryTypes";
import type {
  BackendClusterRequest,
  BackendFileSearchResponse,
  BackendIndexStatus,
  BackendLocalLibraryItem,
  BackendLocalLibraryResponse
} from "./fileLibraryBackendTypes";
import { numberOrZero } from "./mapperPrimitives";

export function mapIndexStatus(status?: BackendIndexStatus | null): IndexStatus | undefined {
  if (!status) return undefined;
  const failure = status.latest_failure;
  return {
    status: String(status.status ?? "empty"),
    filesIndexed: numberOrZero(status.files_indexed),
    chunksIndexed: numberOrZero(status.chunks_indexed),
    embeddingsIndexed: numberOrZero(status.embeddings_indexed),
    bytesIndexed: numberOrZero(status.bytes_indexed),
    lastIndexedAt: String(status.last_indexed_at ?? ""),
    lastModifiedAt: String(status.last_modified_at ?? ""),
    retryHint: String(status.retry_hint ?? ""),
    latestFailure: failure
      ? {
          at: String(failure.at ?? ""),
          pathLabel: String(failure.path_label ?? failure.path ?? ""),
          message: String(failure.message ?? "")
        }
      : null
  };
}

export function mapLocalLibraryResponse(data: BackendLocalLibraryResponse): LocalLibraryResponse {
  const rootCount = numberOrZero(data.scope_summary?.root_count ?? data.roots?.length ?? 0);
  return {
    section: String(data.section ?? "gallery"),
    roots: data.roots ?? [],
    scopeSummary: {
      rootCount,
      rootLabels: (data.scope_summary?.root_labels ?? []).map(String),
      hasAuthorizedRoots: Boolean(data.scope_summary?.has_authorized_roots ?? rootCount > 0),
      displayLabel: String(data.scope_summary?.display_label ?? (rootCount ? `${rootCount} 个授权范围` : "未选择授权目录")),
      rawPathsAvailableForLocalActions: Boolean(data.scope_summary?.raw_paths_available_for_local_actions ?? true),
      shareableSummaryHasRawPaths: Boolean(data.scope_summary?.shareable_summary_has_raw_paths ?? false)
    },
    items: (data.items ?? []).map(mapLocalLibraryItem),
    count: Number(data.count ?? data.items?.length ?? 0),
    total: Number(data.total ?? data.items?.length ?? 0),
    scanned: Number(data.scanned ?? 0),
    truncated: Boolean(data.truncated),
    stats: {
      size: Number(data.stats?.size ?? 0),
      byExtension: data.stats?.by_extension ?? {}
    },
    indexStatus: mapIndexStatus(data.index_status)
  };
}

export function mapLocalLibraryItem(item: BackendLocalLibraryItem): LocalLibraryItem {
  return {
    id: String(item.id ?? item.path),
    path: String(item.path ?? ""),
    pathLabel: String(item.path_label ?? item.name ?? ""),
    name: String(item.name ?? item.path ?? ""),
    parent: String(item.parent ?? ""),
    parentLabel: String(item.parent_label ?? ""),
    kind: String(item.kind ?? "document"),
    extension: String(item.extension ?? ""),
    mimeType: String(item.mime_type ?? ""),
    size: Number(item.size ?? 0),
    createdAt: Number(item.created_at ?? 0),
    modifiedAt: Number(item.modified_at ?? 0),
    previewUrl: String(item.preview_url ?? ""),
    groupLabel: String(item.group_label ?? ""),
    iconUrl: String(item.icon_url ?? ""),
    width: Number(item.width ?? 0),
    height: Number(item.height ?? 0)
  };
}

export function mapFileSearchResponse(data: BackendFileSearchResponse): FileSearchResponse {
  const results: FileSearchResult[] = [
    ...(data.index_results ?? []).map((item, index) => ({
      id: item.file_id ?? `index-${index}`,
      path: item.path,
      match: item.snippet ?? "",
      line: 1,
      score: 0.9
    })),
    ...(data.name_results ?? []).map((item, index) => ({
      id: item.path ?? `name-${index}`,
      path: item.path,
      match: item.name ?? item.path,
      line: 1,
      score: 0.75
    }))
  ];
  const meta = data.name_search ?? {};
  return {
    results,
    meta: {
      count: numberOrZero(meta.count),
      scanned: numberOrZero(meta.scanned),
      truncated: Boolean(meta.truncated),
      status: meta.status ?? "ok",
      indexStatus: mapIndexStatus(data.index_status)
    }
  };
}

export function fileClusterRequestFor(options: FileClusterOptions = {}): BackendClusterRequest {
  const body: BackendClusterRequest = {};
  const groupBy = options.group_by ?? options.groupBy;
  const clusterBy = options.cluster_by ?? options.clusterBy;
  const metadataWeight = options.metadata_weight ?? options.metadataWeight;
  const imagePaths = options.image_paths ?? options.imagePaths;

  if (typeof options.k === "number") body.k = options.k;
  if (groupBy) body.group_by = groupBy;
  if (clusterBy) body.cluster_by = clusterBy;
  if (options.paths?.length) body.paths = options.paths;
  if (imagePaths?.length) body.image_paths = imagePaths;
  if (options.images?.length) body.images = options.images;
  if (typeof options.limit === "number") body.limit = options.limit;
  if (typeof metadataWeight === "number") body.metadata_weight = metadataWeight;

  return body;
}
