import type { TaskCompletionEvidence, TaskResultQuality, TaskResultQualityState } from "../../../shared/executionTypes";
import { recordOrUndefined } from "./mapperPrimitives";
import { zhBackendText } from "../zh";

function normalizeResultQualityState(value: unknown, completionEvidence?: TaskCompletionEvidence): TaskResultQualityState {
  const normalized = String(value ?? "").trim().replace(/[\s.-]+/g, "_").toLowerCase();
  if (normalized === "verified_result") return "verified_result";
  if (normalized === "safe_failure") return "safe_failure";
  if (normalized === "visible_progress") return "visible_progress";
  if (normalized === "task_evidence_only") return "task_evidence_only";
  if (completionEvidence?.status === "verified_completed_result") return "verified_result";
  if (completionEvidence?.status === "safe_failure") return "safe_failure";
  if (completionEvidence?.status === "visible_progress" || completionEvidence?.level === "completed_result") {
    return "visible_progress";
  }
  return "task_evidence_only";
}

export function mapOptionalTaskResultQuality(
  value: unknown,
  completionEvidence?: TaskCompletionEvidence
): TaskResultQuality | undefined {
  if (!recordOrUndefined(value) && !completionEvidence) return undefined;
  return mapTaskResultQuality(value, completionEvidence);
}

export function mapTaskResultQuality(value: unknown, completionEvidence?: TaskCompletionEvidence): TaskResultQuality {
  const record = recordOrUndefined(value);
  const state = normalizeResultQualityState(record?.state, completionEvidence);
  const resultVerified = record?.result_verified === true || record?.resultVerified === true || state === "verified_result";
  const canTreatAsDone =
    record?.can_treat_as_done === true || record?.canTreatAsDone === true || resultVerified;
  const needsReview =
    typeof record?.needs_review === "boolean"
      ? record.needs_review
      : typeof record?.needsReview === "boolean"
        ? record.needsReview
        : !canTreatAsDone;
  const rawMissingChecks = record?.missing_checks ?? record?.missingChecks;
  const missingChecks = Array.isArray(rawMissingChecks)
    ? rawMissingChecks.map((item) => zhBackendText(String(item))).filter(Boolean)
    : completionEvidence?.missing ?? [];

  return {
    state,
    label: zhBackendText(String(record?.label ?? defaultResultQualityLabel(state))),
    summary: zhBackendText(String(record?.summary ?? defaultResultQualitySummary(state))),
    resultVerified,
    canTreatAsDone,
    needsReview,
    missingChecks,
    nextStep: zhBackendText(String(record?.next_step ?? record?.nextStep ?? defaultResultQualityNextStep(state, missingChecks))),
    signoff: Boolean(record?.signoff),
    redacted: record?.redacted !== false,
    privacyNote: zhBackendText(String(record?.privacy_note ?? record?.privacyNote ?? "仅展示脱敏记录状态，不展示原始任务内容。"))
  };
}

function defaultResultQualityLabel(state: TaskResultQualityState): string {
  if (state === "verified_result") return "完成结果已核验";
  if (state === "visible_progress") return "有进度，待核验";
  if (state === "safe_failure") return "安全停止，需处理";
  return "仅有任务记录";
}

function defaultResultQualitySummary(state: TaskResultQualityState): string {
  if (state === "verified_result") return "已看到可复核的最终结果记录。";
  if (state === "visible_progress") return "能看到任务有进展，但还不能当作最终结果。";
  if (state === "safe_failure") return "任务已安全停止，没有形成可验证的最终结果。";
  return "只记录到提交或任务创建，不能当作最终结果。";
}

function defaultResultQualityNextStep(state: TaskResultQualityState, missingChecks: string[]): string {
  if (state === "verified_result") return "查看结果和记录后，再决定是否执行后续操作。";
  if (state === "safe_failure") return "先查看失败或阻塞原因，再调整目标或重试。";
  if (state === "visible_progress") {
    return missingChecks.length
      ? `补齐这些检查后再视为完成：${missingChecks.slice(0, 3).join("、")}。`
      : "打开详情核对时间线记录。";
  }
  return "等待任务继续执行并生成可核验结果。";
}
