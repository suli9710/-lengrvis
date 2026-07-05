export interface FileSearchResult {
  id: string;
  path: string;
  match: string;
  line: number;
  score: number;
}

export interface IndexStatus {
  status: "missing_scope" | "empty" | "ready" | "degraded" | string;
  filesIndexed: number;
  chunksIndexed: number;
  embeddingsIndexed: number;
  bytesIndexed: number;
  lastIndexedAt: string;
  lastModifiedAt: string;
  retryHint: string;
  latestFailure?: {
    at: string;
    pathLabel: string;
    message: string;
  } | null;
}

export interface FileSearchMeta {
  count: number;
  scanned: number;
  truncated: boolean;
  status?: "missing_scope" | "empty_query" | "ok" | string;
  indexStatus?: IndexStatus;
}

export interface FileSearchResponse {
  results: FileSearchResult[];
  meta: FileSearchMeta;
}

export interface LocalLibraryItem {
  id: string;
  path: string;
  pathLabel?: string;
  name: string;
  parent: string;
  parentLabel?: string;
  kind: "image" | "document" | "app" | string;
  extension: string;
  mimeType: string;
  size: number;
  createdAt: number;
  modifiedAt: number;
  previewUrl: string;
  groupLabel: string;
  iconUrl?: string;
  width?: number;
  height?: number;
}

export interface LocalLibraryStats {
  size: number;
  byExtension: Record<string, number>;
}

export interface LocalLibraryScopeSummary {
  rootCount: number;
  rootLabels: string[];
  hasAuthorizedRoots: boolean;
  displayLabel: string;
  rawPathsAvailableForLocalActions: boolean;
  shareableSummaryHasRawPaths: boolean;
}

export interface LocalLibraryResponse {
  section: string;
  roots: string[];
  scopeSummary?: LocalLibraryScopeSummary;
  items: LocalLibraryItem[];
  count: number;
  total: number;
  scanned: number;
  truncated: boolean;
  stats: LocalLibraryStats;
  indexStatus?: IndexStatus;
}

export interface FileRevealResult {
  ok: boolean;
  path?: string;
  revealed?: boolean;
  shown?: boolean;
  error?: string;
}

export interface FileClusterOptions {
  k?: number;
  groupBy?: string;
  group_by?: string;
  clusterBy?: string;
  cluster_by?: string;
  paths?: string[];
  imagePaths?: string[];
  image_paths?: string[];
  images?: string[];
  limit?: number;
  metadataWeight?: number;
  metadata_weight?: number;
}
