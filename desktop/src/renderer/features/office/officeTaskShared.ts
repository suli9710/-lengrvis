import type { TaskEvent } from "../../../shared/executionTypes";

export function sortTasksByUpdatedAt(tasks: TaskEvent[]): TaskEvent[] {
  return [...tasks].sort((a, b) => taskUpdatedAt(b) - taskUpdatedAt(a));
}

export function isRecentTask(task: TaskEvent, hours: number): boolean {
  const updatedAt = taskUpdatedAt(task);
  if (!updatedAt) return false;
  return Date.now() - updatedAt <= hours * 60 * 60 * 1000;
}

function taskUpdatedAt(task: TaskEvent): number {
  const time = Date.parse(task.updatedAt || task.createdAt);
  return Number.isFinite(time) ? time : 0;
}

export function isVerifiedCompletedResult(task: TaskEvent): boolean {
  const evidence = task.completionEvidence;
  return Boolean(task.state === "completed" && evidence?.level === "completed_result" && evidence.resultVerified === true);
}

export function isSafeFailureEvidence(task: TaskEvent): boolean {
  return task.completionEvidence?.level === "safe_failure" || task.completionEvidence?.status === "safe_failure";
}

export function taskDisplayState(task: TaskEvent): string {
  if (isSafeFailureEvidence(task)) return "安全停止";
  if (task.state === "completed") return isVerifiedCompletedResult(task) ? "已完成" : "已结束，待核验";
  if (task.state === "rolled_back") return "已回滚";
  if (task.state === "repair_required") return "回滚需修复";
  if (task.state === "running") return "进行中";
  if (task.state === "blocked") return "待审批";
  if (task.state === "paused") return "已暂停";
  if (task.state === "failed") return "未完成";
  if (task.state === "denied") return "已拒绝";
  if (task.state === "cancelled") return "已取消";
  return "等待中";
}

export function taskDisplayTitle(task: TaskEvent, fallback: string): string {
  const text = task.title.trim();
  if (!text || containsRawTaskInternals(text)) return fallback;
  return text.length > 64 ? `${text.slice(0, 62)}...` : text;
}

function containsRawTaskInternals(value: string): boolean {
  return (
    /[A-Za-z]:\\/.test(value) ||
    /\\\\[^\s]+/.test(value) ||
    /\/(?:Users|home|tmp|var|etc)\//.test(value) ||
    /\b(?:token|api[_-]?key|authorization|tool[_-]?args)\b/i.test(value) ||
    /\b[A-Za-z0-9_-]{48,}\b/.test(value)
  );
}
