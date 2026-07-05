import type { TaskCompletionEvidence } from "../../../shared/executionTypes";
import type { BackendTaskCompletionEvidenceFallback } from "./executionBackendTypes";
import { zhBackendText } from "../zh";

function recordOrUndefined(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function arrayOfObjects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

export function mapOptionalTaskCompletionEvidence(
  value: unknown,
  fallback: BackendTaskCompletionEvidenceFallback = {}
): TaskCompletionEvidence | undefined {
  if (!hasTaskCompletionEvidenceInput(value, fallback)) return undefined;
  return mapTaskCompletionEvidence(value, fallback);
}

export function mapTaskCompletionEvidence(
  value: unknown,
  fallback: BackendTaskCompletionEvidenceFallback = {}
): TaskCompletionEvidence {
  const record = recordOrUndefined(value);
  const evidenceKind = firstNonEmptyString(
    record?.level,
    record?.evidence_kind,
    record?.evidenceKind,
    record?.kind,
    record?.type,
    record?.status,
    typeof value === "string" ? value : undefined,
    fallback.evidenceKind
  ) ?? "";
  const normalizedKind = normalizeCompletionEvidenceKind(evidenceKind);
  const level = taskCompletionEvidenceLevelFromValue(record?.level ?? normalizedKind);
  const resultVerified = booleanOrUndefined(record?.result_verified ?? record?.resultVerified ?? fallback.resultVerified) === true;
  const completedResult = record?.completed_result ?? record?.completedResult ?? fallback.completedResult;
  const hasCompletedResult = hasCompletedResultEvidence(completedResult);
  const status = normalizeTaskCompletionEvidenceStatus(level, normalizedKind, resultVerified, hasCompletedResult);
  const resultArtifacts = arrayOfObjects(record?.result_artifacts ?? record?.resultArtifacts).map((item) => ({
    kind: String(item.kind ?? ""),
    label: zhBackendText(String(item.label ?? "")),
    redacted: item.redacted !== false,
    count: Number.isFinite(Number(item.count)) ? Number(item.count) : undefined
  }));
  const missing = Array.isArray(record?.missing)
    ? record.missing.map((item) => zhBackendText(String(item))).filter(Boolean)
    : taskCompletionEvidenceMissing(status, resultVerified, hasCompletedResult);
  const signoff = Boolean(record?.signoff);
  return {
    level,
    status,
    evidenceKind: normalizedKind,
    resultVerified,
    resultArtifacts: resultArtifacts.length ? resultArtifacts : taskCompletionEvidenceArtifacts(status),
    missing,
    signoff,
    summary: taskCompletionEvidenceSummary(status, missing),
    privacyNote: "仅展示证据状态，不展示原始证据内容。"
  };
}

export function hasTaskCompletionEvidenceInput(value: unknown, fallback: BackendTaskCompletionEvidenceFallback): boolean {
  return Boolean(
    recordOrUndefined(value) ||
      (typeof value === "string" && value.trim()) ||
      fallback.resultVerified !== undefined ||
      fallback.completedResult !== undefined ||
      fallback.evidenceKind !== undefined
  );
}

export function taskCompletionEvidenceLevelFromValue(value: unknown): TaskCompletionEvidence["level"] {
  const kind = normalizeCompletionEvidenceKind(String(value ?? ""));
  if (kind === "completed_result" || kind === "verified_completed_result") return "completed_result";
  if (kind === "safe_failure" || kind === "failed_safely" || kind === "safe_failed") return "safe_failure";
  if (kind === "visible_progress" || kind === "progress" || kind === "tool_progress") return "visible_progress";
  if (kind === "task_created" || kind === "task_evidence" || kind === "task_evidence_only") return "task_created";
  return "submission";
}

export function normalizeTaskCompletionEvidenceStatus(
  level: TaskCompletionEvidence["level"],
  kind: string,
  resultVerified: boolean,
  hasCompletedResult: boolean
): TaskCompletionEvidence["status"] {
  if (level === "completed_result" && resultVerified) return "verified_completed_result";
  if (level === "safe_failure") return "safe_failure";
  if (level === "visible_progress") return "visible_progress";
  if (level === "submission" || level === "task_created") return "task_evidence_only";
  if (kind === "safe_failure" || kind === "failed_safely" || kind === "safe_failed") return "safe_failure";
  if (kind === "visible_progress" || kind === "progress" || kind === "tool_progress") return "visible_progress";
  if (
    kind === "task_evidence_only" ||
    kind === "task_evidence" ||
    kind === "evidence_only" ||
    kind === "submission" ||
    kind === "task_submission" ||
    kind === "command_submission" ||
    kind.includes("submission") ||
    kind.includes("task_evidence")
  ) {
    return "task_evidence_only";
  }
  if ((kind === "completed_result" || kind === "verified_completed_result" || hasCompletedResult) && resultVerified) {
    return "verified_completed_result";
  }
  return "unverified";
}

export function taskCompletionEvidenceSummary(status: TaskCompletionEvidence["status"], missing: string[] = []): string {
  switch (status) {
    case "verified_completed_result":
      return "已看到可复核的最终结果记录，系统确认它不是仅提交或过程进度。";
    case "task_evidence_only":
      return "只记录到提交或任务过程证据，不能当作最终结果。";
    case "visible_progress":
      return missing.length
        ? `能看到任务有进展，但还缺少 ${missing.slice(0, 2).join("、")}。`
        : "能看到任务有进展，但还没有最终结果验证。";
    case "safe_failure":
      return "任务安全失败，没有可验证的最终结果。";
    default:
      return "还没有可验证的最终结果证据。";
  }
}

export function taskCompletionEvidenceArtifacts(status: TaskCompletionEvidence["status"]): TaskCompletionEvidence["resultArtifacts"] {
  if (status === "verified_completed_result") {
    return [{ kind: "completed_result", label: "最终结果证据已脱敏记录", redacted: true }];
  }
  if (status === "visible_progress") {
    return [{ kind: "visible_progress", label: "可见进度证据已脱敏记录", redacted: true }];
  }
  if (status === "task_evidence_only") {
    return [{ kind: "task_evidence", label: "任务过程证据已脱敏记录", redacted: true }];
  }
  if (status === "safe_failure") {
    return [{ kind: "safe_failure", label: "安全失败记录已脱敏", redacted: true }];
  }
  return [];
}

export function taskCompletionEvidenceMissing(
  status: TaskCompletionEvidence["status"],
  resultVerified: boolean,
  hasCompletedResult: boolean
): string[] {
  if (status === "verified_completed_result") return [];
  const missing = [];
  if (!hasCompletedResult) missing.push("最终结果记录");
  if (!resultVerified) missing.push("结果复核确认");
  return missing;
}

export function normalizeCompletionEvidenceKind(value: string): string {
  return value.trim().toLowerCase().replace(/[\s.-]+/g, "_");
}

export function hasCompletedResultEvidence(value: unknown): boolean {
  if (value === undefined || value === null || value === false) return false;
  return !(typeof value === "string" && !value.trim());
}

export function firstNonEmptyString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

export function booleanOrUndefined(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes"].includes(normalized)) return true;
    if (["false", "0", "no"].includes(normalized)) return false;
  }
  return undefined;
}
