export interface BackendIndexStatus {
  status?: string;
  files_indexed?: number | string;
  chunks_indexed?: number | string;
  embeddings_indexed?: number | string;
  bytes_indexed?: number | string;
  last_indexed_at?: string;
  last_modified_at?: string;
  retry_hint?: string;
  latest_failure?: {
    at?: string;
    path_label?: string;
    path?: string;
    message?: string;
  } | null;
}

export interface BackendFileSearchResponse {
  index_results?: Array<{ file_id?: string; path: string; snippet?: string }>;
  name_results?: Array<{ path: string; name?: string }>;
  index_status?: BackendIndexStatus;
  name_search?: {
    count?: number | string;
    scanned?: number | string;
    truncated?: boolean;
    status?: string;
  };
}

export interface BackendLocalLibraryItem {
  id: string;
  path: string;
  path_label?: string;
  name: string;
  parent: string;
  parent_label?: string;
  kind: string;
  extension: string;
  mime_type?: string;
  size?: number;
  created_at?: number;
  modified_at?: number;
  preview_url?: string;
  group_label?: string;
  icon_url?: string;
  width?: number;
  height?: number;
}

export interface BackendLocalLibraryResponse {
  section: string;
  roots?: string[];
  scope_summary?: {
    root_count?: number | string;
    root_labels?: string[];
    has_authorized_roots?: boolean;
    display_label?: string;
    raw_paths_available_for_local_actions?: boolean;
    shareable_summary_has_raw_paths?: boolean;
  };
  items?: BackendLocalLibraryItem[];
  count?: number;
  total?: number;
  scanned?: number;
  truncated?: boolean;
  stats?: {
    size?: number;
    by_extension?: Record<string, number>;
  };
  index_status?: BackendIndexStatus;
}

export interface BackendClusterEntry {
  cluster_id: number | string;
  size: number;
  preview: string[];
  suggested_name?: string;
  group_by?: string;
  group_value?: string;
}

export interface BackendClusterResponse {
  ok: boolean;
  clusters: BackendClusterEntry[];
  count?: number;
  total?: number;
  method?: string;
  group_by?: string;
  cluster_by?: string;
  error?: string;
}

export interface BackendClusterRequest {
  k?: number;
  group_by?: string;
  cluster_by?: string;
  paths?: string[];
  image_paths?: string[];
  images?: string[];
  limit?: number;
  metadata_weight?: number;
}

export interface BackendFileRevealResult {
  ok?: boolean;
  path?: string;
  revealed?: boolean;
  shown?: boolean;
  error?: string;
}
