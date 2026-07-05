export type CleanupDisposition = "permanent_delete" | "trash" | "suggestion_only" | "skip" | string;

export interface CleanupItem {
  id: string;
  path: string;
  name?: string;
  action: string;
  disposition: CleanupDisposition;
  bucket?: "direct_delete" | "recycle_bin" | "suggestion_only" | "immediate" | "approval" | "info_only" | string;
  sizeBytes?: number;
  sizeMb?: number;
  category?: string;
  detail?: string;
  reason?: string;
  riskLevel?: "low" | "medium" | "high" | "critical" | string;
  canRollback?: boolean;
  selected?: boolean;
  modifiedAt?: string;
  metadata?: Record<string, unknown>;
}

export interface CleanupPlan {
  id: string;
  contentHash?: string;
  title: string;
  summary?: string;
  status?: "draft" | "needs_approval" | "approved" | "executed" | "rolled_back" | string;
  createdAt?: string;
  updatedAt?: string;
  totalBytes?: number;
  reclaimableBytes?: number;
  permanentDeleteBytes?: number;
  trashBytes?: number;
  riskWarnings: string[];
  items: CleanupItem[];
}

export interface CleanupScanRequest {
  roots?: string[];
  thresholdMb?: number;
  includeCaches?: boolean;
}

export interface CleanupPlanRequest extends CleanupScanRequest {
  itemIds?: string[];
  preferTrash?: boolean;
}

export interface CleanupExecuteRequest {
  planId?: string;
  contentHash?: string;
  selectedItemIds?: string[];
  roots?: string[];
  items?: CleanupItem[];
  dryRun?: boolean;
  approved?: boolean;
  approvalId?: string;
}

export interface CleanupRollbackRequest {
  planId?: string;
  executionId?: string;
}

export interface CleanupExecutionResult {
  ok: boolean;
  planId?: string;
  executionId?: string;
  freedBytes?: number;
  executed: CleanupItem[];
  rolledBack?: CleanupItem[];
  errors?: string[];
}
