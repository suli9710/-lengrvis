import type { TaskEvent, TaskResultQuality, TaskResultQualityState } from "../../../shared/executionTypes";
import { taskDisplayTitle } from "../office/officeTaskShared";

export type TaskResultTimelineTone = "idle" | "active" | "ready" | "warning" | "blocked" | "failed";
export type TaskResultTimelineStepState = "idle" | "current" | "done" | "blocked" | "failed";

export interface TaskResultTimelineStep {
  id: "understand" | "scope" | "execute" | "verify";
  label: string;
  detail: string;
  state: TaskResultTimelineStepState;
}

export interface TaskResultTimelineSummary {
  task: TaskEvent | null;
  title: string;
  statusLabel: string;
  detail: string;
  action: "open" | "approve" | "compose";
  actionLabel: string;
  tone: TaskResultTimelineTone;
  resultState: TaskResultQualityState | "none";
  canTreatAsDone: boolean;
  missingChecks: string[];
  nextStep: string;
  privacyNote: string;
  steps: TaskResultTimelineStep[];
}

export function buildTaskResultTimelineSummary(tasks: TaskEvent[], hasDraft = false): TaskResultTimelineSummary {
  const task = latestTask(tasks);
  if (!task) {
    return {
      task: null,
      title: hasDraft ? "准备启动任务" : "等待第一个任务",
      statusLabel: hasDraft ? "待发送" : "空闲",
      detail: hasDraft ? "发送后会先理解目标、确认范围，再开始执行。" : "任务启动后，这里会显示可复核的结果时间线。",
      action: "compose",
      actionLabel: hasDraft ? "发送后开始" : "输入目标",
      tone: hasDraft ? "active" : "idle",
      resultState: "none",
      canTreatAsDone: false,
      missingChecks: [],
      nextStep: hasDraft ? "发送后开始执行。" : "输入目标后开始。",
      privacyNote: "任务记录默认只显示脱敏摘要。",
      steps: [
        { id: "understand", label: "理解目标", detail: hasDraft ? "已准备分析" : "等待输入", state: hasDraft ? "current" : "idle" },
        { id: "scope", label: "确认范围", detail: "检查权限和风险", state: "idle" },
        { id: "execute", label: "执行记录", detail: "运行后显示进度", state: "idle" },
        { id: "verify", label: "结果复核", detail: "完成后核对记录", state: "idle" }
      ]
    };
  }

  const quality = task.resultQuality;
  const resultState = quality?.state ?? stateFromCompletion(task);
  const canTreatAsDone = quality?.canTreatAsDone ?? resultState === "verified_result";
  const missingChecks = quality?.missingChecks ?? task.completionEvidence?.missing ?? [];
  const safeFailure = resultState === "safe_failure";
  const blocked = task.state === "blocked";
  const failed = task.state === "failed" || task.state === "repair_required" || safeFailure;
  const rolledBack = task.state === "rolled_back";
  const verified = resultState === "verified_result" && canTreatAsDone;
  const visibleProgress = resultState === "visible_progress";
  const evidenceOnly = resultState === "task_evidence_only";

  return {
    task,
    title: safeTaskTitle(task),
    statusLabel: resultStatusLabel(task, quality, resultState, canTreatAsDone),
    detail: resultDetail(task, quality, resultState),
    action: blocked ? "approve" : "open",
    actionLabel: blocked ? "去确认" : failed ? "查看原因" : verified ? "查看结果" : "核对时间线",
    tone: blocked ? "blocked" : failed ? "failed" : verified || rolledBack ? "ready" : task.state === "running" || task.state === "queued" ? "active" : "warning",
    resultState,
    canTreatAsDone,
    missingChecks,
    nextStep: quality?.nextStep ?? defaultNextStep(task, resultState, blocked, failed, verified),
    privacyNote: quality?.privacyNote ?? task.completionEvidence?.privacyNote ?? "仅展示记录状态，不展示原始任务内容。",
    steps: [
      { id: "understand", label: "理解目标", detail: "已接收并拆解", state: "done" },
      {
        id: "scope",
        label: "确认范围",
        detail: blocked ? "等待你确认" : "权限和风险已记录",
        state: blocked ? "blocked" : "done"
      },
      {
        id: "execute",
        label: "执行记录",
        detail: executeStepDetail(task, visibleProgress, evidenceOnly),
        state: executeStepState(task, failed, blocked)
      },
      {
        id: "verify",
        label: "结果复核",
        detail: verifyStepDetail(quality, verified, safeFailure, missingChecks),
        state: verifyStepState(task, verified, safeFailure, missingChecks)
      }
    ]
  };
}

function latestTask(tasks: TaskEvent[]): TaskEvent | null {
  return [...tasks].sort((left, right) => taskUpdatedAt(right) - taskUpdatedAt(left))[0] ?? null;
}

function taskUpdatedAt(task: TaskEvent): number {
  const time = Date.parse(task.updatedAt || task.createdAt);
  return Number.isFinite(time) ? time : 0;
}

function stateFromCompletion(task: TaskEvent): TaskResultQualityState {
  if (task.completionEvidence?.status === "verified_completed_result") return "verified_result";
  if (task.completionEvidence?.status === "safe_failure" || task.completionEvidence?.level === "safe_failure") return "safe_failure";
  if (task.completionEvidence?.status === "visible_progress" || task.completionEvidence?.level === "completed_result") return "visible_progress";
  return "task_evidence_only";
}

function resultStatusLabel(
  task: TaskEvent,
  quality: TaskResultQuality | undefined,
  resultState: TaskResultQualityState,
  canTreatAsDone: boolean
): string {
  if (resultState === "verified_result" && canTreatAsDone) return "完成结果已核验";
  if (resultState === "safe_failure") return "安全停止";
  if (task.state === "blocked") return "等待确认";
  if (task.state === "running") return "正在处理";
  if (task.state === "queued") return "等待执行";
  if (task.state === "paused") return "已暂停";
  if (task.state === "rolled_back") return "已回滚";
  if (task.state === "repair_required") return "回滚需修复";
  if (task.state === "failed") return "未完成";
  if (resultState === "visible_progress") return "有进度，待核验";
  if (resultState === "task_evidence_only") return "仅有任务记录";
  if (quality?.state === "verified_result") return "已结束，待核验";
  return task.state === "completed" ? "已结束，待核验" : "等待处理";
}

function resultDetail(task: TaskEvent, quality: TaskResultQuality | undefined, resultState: TaskResultQualityState): string {
  if (quality?.summary) return quality.summary;
  if (task.completionEvidence?.summary) return task.completionEvidence.summary;
  if (resultState === "verified_result") return "结果已核验，可以作为完成记录。";
  if (resultState === "safe_failure") return "任务安全停止，没有形成可核验结果。";
  if (resultState === "visible_progress") return "任务已有可见进度，还需要最终复核。";
  if (resultState === "task_evidence_only") return "任务只有提交或创建记录，不能当作完成结果。";
  if (task.state === "blocked") return "任务已停在确认点，批准前不会继续执行。";
  if (task.state === "running" || task.state === "queued") return "任务正在推进，结果出现前会继续显示进度。";
  if (task.state === "failed") return "任务没有形成可核验结果，请先查看原因。";
  if (task.state === "rolled_back") return "已按回滚记录恢复变更，并完成资源后态重读核验。";
  if (task.state === "repair_required") return "回滚没有完整恢复变更，需要查看记录并完成剩余修复。";
  if (task.state === "completed") return "任务状态已结束，仍需核对是否具备完成结果记录。";
  return task.description || "等待任务产生可复核记录。";
}

function defaultNextStep(
  task: TaskEvent,
  resultState: TaskResultQualityState,
  blocked: boolean,
  failed: boolean,
  verified: boolean
): string {
  if (blocked) return "先确认审批项。";
  if (verified) return "查看结果记录。";
  if (failed || resultState === "safe_failure") return "查看原因后重试。";
  if (task.state === "running" || task.state === "queued") return "等待任务继续执行。";
  if (resultState === "visible_progress") return "核对结果或重新检查。";
  return "等待可核验结果记录。";
}

function executeStepState(task: TaskEvent, failed: boolean, blocked: boolean): TaskResultTimelineStepState {
  if (failed) return "failed";
  if (blocked) return "blocked";
  if (task.state === "running" || task.state === "queued") return "current";
  if (task.state === "paused") return "blocked";
  if (task.state === "completed" || task.state === "rolled_back") return "done";
  return "idle";
}

function executeStepDetail(task: TaskEvent, visibleProgress: boolean, evidenceOnly: boolean): string {
  if (task.state === "queued") return "排队中";
  if (task.state === "running") return "正在记录过程";
  if (task.state === "paused") return "进度已保留";
  if (task.state === "blocked") return "暂停等待审批";
  if (task.state === "failed") return "没有完成";
  if (task.state === "rolled_back") return "变更已恢复";
  if (task.state === "repair_required") return "恢复不完整";
  if (visibleProgress) return "已有进度记录";
  if (evidenceOnly) return "只有提交记录";
  return "执行已结束";
}

function verifyStepState(
  task: TaskEvent,
  verified: boolean,
  safeFailure: boolean,
  missingChecks: string[]
): TaskResultTimelineStepState {
  if (verified) return "done";
  if (safeFailure || task.state === "failed" || task.state === "repair_required") return "failed";
  if (task.state === "completed" || task.state === "rolled_back" || missingChecks.length > 0) return "blocked";
  if (task.state === "running" || task.state === "queued") return "idle";
  return "idle";
}

function verifyStepDetail(
  quality: TaskResultQuality | undefined,
  verified: boolean,
  safeFailure: boolean,
  missingChecks: string[]
): string {
  if (verified) return "结果可作为完成记录";
  if (safeFailure) return "安全停止，不能视为完成";
  if (missingChecks.length) return `缺 ${missingChecks.slice(0, 2).join("、")}`;
  if (quality?.nextStep) return quality.nextStep;
  return "等待复核记录";
}

function safeTaskTitle(task: TaskEvent): string {
  return taskDisplayTitle(task, "最近任务");
}
