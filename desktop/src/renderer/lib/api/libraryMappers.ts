import type { IndexStatus, LocalLibraryItem, LocalLibraryResponse } from "../../../shared/types";
import type { BackendIndexStatus, BackendLocalLibraryItem, BackendLocalLibraryResponse } from "./backendTypes";
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
