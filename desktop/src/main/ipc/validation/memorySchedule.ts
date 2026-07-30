import type {
  DesktopMemoryRecallRequest,
  DesktopMemoryReviewRequest,
  DesktopMemorySaveRequest,
  DesktopScheduleCreateRequest,
  DesktopScheduleEnableRequest
} from "../../../shared/types";
import {
  rejectUnexpectedBridgeKeys,
  validateBridgeBoolean,
  validateBridgeEnum,
  validateBridgeIdentifier,
  validateBridgePositiveInteger,
  validateBridgeStringValue,
  validateOptionalBridgeIdentifier,
  validateOptionalStringList,
  validatePlainBridgeBody
} from "./primitives";

const RUN_MODES = new Set(["privacy", "efficiency", "hybrid"]);
const MEMORY_CONFLICT_STATUSES = new Set(["none", "conflicting", "resolved", "superseded"]);

export function validateMemorySaveRequest(value: unknown): DesktopMemorySaveRequest {
  const request = validatePlainBridgeBody(value, "memory save request");
  rejectUnexpectedBridgeKeys(request, new Set(["content", "tags", "taskId", "task_id", "kind"]), "memory save request");
  const content = validateBridgeStringValue(request.content, "memory content", 8_000, { allowEmpty: false, trim: true });
  return {
    content,
    tags: validateOptionalStringList(request.tags, "memory tags", 20, 64),
    taskId: request.taskId === undefined && request.task_id === undefined
      ? undefined
      : validateOptionalBridgeIdentifier(request.taskId ?? request.task_id, "memory task id"),
    kind: request.kind === undefined || request.kind === null || request.kind === ""
      ? undefined
      : validateBridgeStringValue(request.kind, "memory kind", 64, { allowEmpty: false, trim: true })
  };
}

export function validateMemoryRecallRequest(value: unknown): DesktopMemoryRecallRequest {
  const request = validatePlainBridgeBody(value, "memory recall request");
  rejectUnexpectedBridgeKeys(request, new Set(["query", "k", "tags"]), "memory recall request");
  return {
    query: validateBridgeStringValue(request.query, "memory recall query", 2_000, { allowEmpty: false, trim: true }),
    k: validateBridgePositiveInteger(request.k, "memory recall k", 5, 1, 50),
    tags: validateOptionalStringList(request.tags, "memory recall tags", 20, 64)
  };
}

export function validateMemoryReviewRequest(value: unknown): DesktopMemoryReviewRequest {
  const request = validatePlainBridgeBody(value, "memory review request");
  rejectUnexpectedBridgeKeys(
    request,
    new Set(["memoryId", "memory_id", "reviewedBy", "reviewed_by", "conflictStatus", "conflict_status", "resolveConflict"]),
    "memory review request"
  );
  const rawConflictStatus = request.conflictStatus ?? request.conflict_status;
  const conflictStatus = rawConflictStatus === undefined || rawConflictStatus === null || rawConflictStatus === ""
    ? undefined
    : validateBridgeEnum<NonNullable<DesktopMemoryReviewRequest["conflictStatus"]>>(
        rawConflictStatus,
        "memory conflict status",
        MEMORY_CONFLICT_STATUSES
      );
  const reviewedByValue = request.reviewedBy ?? request.reviewed_by;
  return {
    memoryId: validateBridgeIdentifier(request.memoryId ?? request.memory_id, "memory id"),
    reviewedBy: reviewedByValue === undefined || reviewedByValue === null || reviewedByValue === ""
      ? undefined
      : validateBridgeStringValue(reviewedByValue, "memory reviewer", 128, { allowEmpty: false, trim: true }),
    conflictStatus,
    resolveConflict: request.resolveConflict === undefined
      ? undefined
      : validateBridgeBoolean(request.resolveConflict, "memory conflict resolution")
  };
}

export function validateScheduleCreateRequest(value: unknown): DesktopScheduleCreateRequest {
  const request = validatePlainBridgeBody(value, "schedule create request");
  rejectUnexpectedBridgeKeys(request, new Set(["cron", "goal", "mode", "note"]), "schedule create request");
  const cron = validateBridgeStringValue(request.cron, "schedule cron", 256, { allowEmpty: false, trim: true });
  const goal = validateBridgeStringValue(request.goal, "schedule goal", 20_000, { allowEmpty: false, trim: true });
  const mode = validateBridgeEnum<DesktopScheduleCreateRequest["mode"] & string>(
    request.mode,
    "schedule mode",
    RUN_MODES,
    "efficiency"
  );
  const note =
    request.note === undefined || request.note === null
      ? undefined
      : validateBridgeStringValue(request.note, "schedule note", 2_000, { allowEmpty: true, trim: true });
  return note === undefined ? { cron, goal, mode } : { cron, goal, mode, note };
}

export function validateScheduleEnableRequest(scheduleId: unknown, enabled: unknown): DesktopScheduleEnableRequest {
  return {
    scheduleId: validateBridgeIdentifier(scheduleId, "schedule id"),
    enabled: validateBridgeBoolean(enabled, "schedule enabled")
  };
}
