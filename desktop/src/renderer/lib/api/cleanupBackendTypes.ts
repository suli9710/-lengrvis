export interface BackendCleanupScanRequest {
  roots?: string[];
  threshold_mb?: number;
  include_caches?: boolean;
}

export interface BackendCleanupPlanRequest extends BackendCleanupScanRequest {
  item_ids?: string[];
  prefer_trash?: boolean;
}

export interface BackendCleanupExecuteRequest {
  roots?: string[];
  plan_id?: string;
  content_hash?: string;
  selected_item_ids?: string[];
  dry_run?: boolean;
  approved?: boolean;
  approval_id?: string;
}

export interface BackendCleanupRollbackRequest {
  plan_id?: string;
  execution_id?: string;
}

export interface BackendCleanupItem {
  id?: string;
  path?: string;
  name?: string;
  action?: string;
  disposition?: string;
  mode?: string;
  delete_mode?: string;
  bucket?: string;
  size_bytes?: number;
  sizeBytes?: number;
  bytes?: number;
  size_mb?: number;
  sizeMb?: number;
  category?: string;
  detail?: string;
  description?: string;
  reason?: string;
  risk_level?: string;
  riskLevel?: string;
  can_rollback?: boolean;
  canRollback?: boolean;
  selected?: boolean;
  modified_at?: string;
  modifiedAt?: string;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface BackendCleanupPlan {
  id?: string;
  plan_id?: string;
  content_hash?: string;
  contentHash?: string;
  title?: string;
  summary?: string;
  detail?: string;
  status?: string;
  created_at?: string;
  createdAt?: string;
  updated_at?: string;
  updatedAt?: string;
  total_bytes?: number;
  totalBytes?: number;
  reclaimable_bytes?: number;
  reclaimableBytes?: number;
  freed_bytes?: number;
  freedBytes?: number;
  permanent_delete_bytes?: number;
  permanentDeleteBytes?: number;
  trash_bytes?: number;
  trashBytes?: number;
  risk_warnings?: unknown;
  riskWarnings?: unknown;
  warnings?: unknown;
  items?: BackendCleanupItem[];
  buckets?: Record<string, unknown>;
  cleanup_plan?: unknown;
  plan?: unknown;
}

export interface BackendCleanupExecutionResult {
  ok?: boolean;
  plan_id?: string;
  planId?: string;
  execution_id?: string;
  executionId?: string;
  freed_bytes?: number;
  freedBytes?: number;
  executed?: unknown;
  rolled_back?: unknown;
  rolledBack?: unknown;
  errors?: unknown;
}
