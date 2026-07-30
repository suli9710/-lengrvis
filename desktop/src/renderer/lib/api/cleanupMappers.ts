import type { CleanupExecutionResult, CleanupItem, CleanupPlan, CleanupScanRequest } from "../../../shared/cleanupTypes";
import type {
  BackendCleanupExecutionResult,
  BackendCleanupItem,
  BackendCleanupPlan,
  BackendCleanupScanRequest
} from "./cleanupBackendTypes";
import {
  arrayOfObjects,
  numberOrUndefined,
  optionalString,
  recordOrUndefined,
  stringArray
} from "./mapperPrimitives";

export function cleanupScanRequestFor(body: CleanupScanRequest): BackendCleanupScanRequest {
  return {
    roots: body.roots,
    threshold_mb: body.thresholdMb,
    include_caches: body.includeCaches
  };
}

export function mapCleanupPlan(input: BackendCleanupPlan): CleanupPlan {
  const plan = normalizeCleanupPlan(input);
  return {
    id: String(plan.id ?? plan.plan_id ?? crypto.randomUUID()),
    contentHash: optionalString(plan.content_hash ?? plan.contentHash),
    title: String(plan.title ?? "清理计划"),
    summary: optionalString(plan.summary ?? plan.detail),
    status: optionalString(plan.status),
    createdAt: optionalString(plan.created_at ?? plan.createdAt),
    updatedAt: optionalString(plan.updated_at ?? plan.updatedAt),
    totalBytes: numberOrUndefined(plan.total_bytes ?? plan.totalBytes),
    reclaimableBytes: numberOrUndefined(plan.reclaimable_bytes ?? plan.reclaimableBytes ?? plan.freed_bytes ?? plan.freedBytes),
    permanentDeleteBytes: numberOrUndefined(plan.permanent_delete_bytes ?? plan.permanentDeleteBytes),
    trashBytes: numberOrUndefined(plan.trash_bytes ?? plan.trashBytes),
    riskWarnings: stringArray(plan.risk_warnings ?? plan.riskWarnings ?? plan.warnings),
    items: cleanupItemsForPlan(plan)
  };
}

export function normalizeCleanupPlan(input: BackendCleanupPlan): BackendCleanupPlan {
  if (input && typeof input === "object" && input.cleanup_plan && typeof input.cleanup_plan === "object") {
    return input.cleanup_plan as BackendCleanupPlan;
  }
  if (input && typeof input === "object" && input.plan && typeof input.plan === "object") {
    return input.plan as BackendCleanupPlan;
  }
  return input;
}

export function cleanupItemsForPlan(plan: BackendCleanupPlan): CleanupItem[] {
  const direct = arrayOfObjects(plan.items).map((item) => mapCleanupItem(item, "suggestion_only"));
  if (direct.length) return direct;

  const buckets = plan.buckets && typeof plan.buckets === "object" ? plan.buckets : {};
  return [
    ...arrayOfObjects(buckets.direct_delete ?? buckets.permanent_delete).map((item) => mapCleanupItem(item, "direct_delete")),
    ...arrayOfObjects(buckets.recycle_bin ?? buckets.trash).map((item) => mapCleanupItem(item, "recycle_bin")),
    ...arrayOfObjects(buckets.suggestion_only ?? buckets.info_only).map((item) => mapCleanupItem(item, "suggestion_only")),
    ...arrayOfObjects(buckets.immediate).map((item) => mapCleanupItem(item, "recycle_bin")),
    ...arrayOfObjects(buckets.approval).map((item) => mapCleanupItem(item, "recycle_bin"))
  ];
}

export function mapCleanupItem(item: BackendCleanupItem, fallbackBucket: string): CleanupItem {
  const bucket = String(item.bucket ?? fallbackBucket);
  const action = String(item.action ?? "");
  const disposition = cleanupDispositionFor(item, bucket, action);
  const sizeBytes = numberOrUndefined(item.size_bytes ?? item.sizeBytes ?? item.bytes);
  const sizeMb = numberOrUndefined(item.size_mb ?? item.sizeMb);
  return {
    id: String(item.id ?? item.path ?? crypto.randomUUID()),
    path: String(item.path ?? ""),
    name: optionalString(item.name),
    action,
    disposition,
    bucket,
    sizeBytes: sizeBytes ?? (sizeMb === undefined ? undefined : Math.round(sizeMb * 1024 * 1024)),
    sizeMb,
    category: optionalString(item.category),
    detail: optionalString(item.detail ?? item.description),
    reason: optionalString(item.reason),
    riskLevel: optionalString(item.risk_level ?? item.riskLevel),
    canRollback: item.can_rollback === undefined && item.canRollback === undefined
      ? disposition === "trash"
      : Boolean(item.can_rollback ?? item.canRollback),
    selected: item.selected === undefined ? undefined : Boolean(item.selected),
    modifiedAt: optionalString(item.modified_at ?? item.modifiedAt),
    metadata: recordOrUndefined(item.metadata)
  };
}

export function cleanupDispositionFor(item: BackendCleanupItem, bucket: string, action: string): CleanupItem["disposition"] {
  const explicit = item.disposition ?? item.mode ?? item.delete_mode;
  if (typeof explicit === "string" && explicit) return explicit;
  const normalized = `${bucket} ${action}`.toLowerCase();
  if (normalized.includes("permanent") || normalized.includes("direct_delete") || normalized.includes("delete_permanent")) {
    return "permanent_delete";
  }
  if (normalized.includes("info_only") || normalized.includes("suggestion") || normalized.includes("review")) {
    return "suggestion_only";
  }
  if (normalized.includes("trash") || normalized.includes("recycle") || normalized.includes("cache") || normalized.includes("temp")) {
    return "trash";
  }
  return "suggestion_only";
}

export function mapCleanupExecutionResult(result: BackendCleanupExecutionResult): CleanupExecutionResult {
  return {
    ok: result.ok !== false,
    planId: optionalString(result.plan_id ?? result.planId),
    executionId: optionalString(result.execution_id ?? result.executionId),
    freedBytes: numberOrUndefined(result.freed_bytes ?? result.freedBytes),
    executed: arrayOfObjects(result.executed).map((item) => mapCleanupItem(item, "recycle_bin")),
    rolledBack: arrayOfObjects(result.rolled_back ?? result.rolledBack).map((item) => mapCleanupItem(item, "recycle_bin")),
    errors: stringArray(result.errors)
  };
}

export function cleanupPlanFromApprovalPayload(payload: unknown): CleanupPlan | undefined {
  const candidate = findCleanupPayload(payload);
  if (!candidate) return undefined;
  const plan = mapCleanupPlan(candidate);
  return plan.items.length ? plan : undefined;
}

export function findCleanupPayload(value: unknown): BackendCleanupPlan | undefined {
  if (!value || typeof value !== "object") return undefined;
  if (Array.isArray(value)) {
    const items = value.filter((item): item is BackendCleanupItem => Boolean(item && typeof item === "object"));
    return items.some(looksLikeCleanupItem) ? { items } : undefined;
  }

  const record = value as Record<string, unknown>;
  for (const key of ["cleanup_plan", "cleanupPlan", "plan", "diff_preview", "payload"]) {
    const nested = findCleanupPayload(record[key]);
    if (nested) return nested;
  }

  if (looksLikeCleanupPlan(record)) return record as BackendCleanupPlan;
  return undefined;
}

export function looksLikeCleanupPlan(record: Record<string, unknown>): boolean {
  if (Array.isArray(record.items) && record.items.some(looksLikeCleanupItem)) return true;
  const buckets = record.buckets;
  return Boolean(
    buckets &&
      typeof buckets === "object" &&
      ["direct_delete", "permanent_delete", "recycle_bin", "trash", "suggestion_only", "info_only", "immediate", "approval"]
        .some((key) => Array.isArray((buckets as Record<string, unknown>)[key]))
  );
}

export function looksLikeCleanupItem(value: unknown): value is BackendCleanupItem {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  const action = String(record.action ?? record.disposition ?? record.bucket ?? "").toLowerCase();
  return Boolean(record.path && (
    action.includes("clean") ||
    action.includes("delete") ||
    action.includes("trash") ||
    action.includes("cache") ||
    action.includes("review") ||
    action.includes("recycle")
  ));
}
