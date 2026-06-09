import type { MobileTask, MobileTaskAction } from "./api/client";
import { safeCompactText, safeDisplayText } from "./safeDisplay";

const TERMINAL_TASK_STATUSES = ["completed", "failed", "cancelled", "denied"];

export function taskDisplayTitle(task: MobileTask): string {
  return safeCompactText(task.title, "电脑任务");
}

export function taskDisplaySummary(task: MobileTask): string {
  if (task.privacy_redacted) {
    return "隐私任务：手机只显示状态，内容请在电脑端查看。";
  }
  if (task.summary) {
    return safeDisplayText(task.summary, task.content_redacted ? "摘要已脱敏，细节请在电脑端核对。" : "正在等待电脑端更新摘要。");
  }
  if (task.content_redacted) return "摘要已脱敏，细节请在电脑端核对。";
  return "正在等待电脑端更新摘要。";
}

export function taskStatusDetailText(task: MobileTask): string {
  if (task.status_detail) return safeDisplayText(task.status_detail, statusFallback(task.status));
  return statusFallback(task.status);
}

export function taskCredibilityText(task: MobileTask): string {
  if (task.privacy_redacted) return "隐私内容已隐藏，手机只显示状态。";
  if (task.content_redacted) return "摘要已脱敏，细节需在电脑端核对。";
  if (hasVerifiedCompletedResult(task)) return "已带可核对的完成结果。";
  if (task.completion_evidence?.level === "safe_failure") return "未完成；电脑端记录了失败或阻断原因。";
  if (task.completion_evidence?.level === "visible_progress") return "已有进度；手机未收到已核验结果。";
  if (task.completion_evidence?.level === "task_created") return "仅收到任务记录，还没有结果。";
  if (task.status === "completed") return "已结束；手机未收到可核验证据。";
  if (task.status === "failed") return "未完成；不要把当前摘要当作结果。";
  if (task.status === "cancelled" || task.status === "denied") return "已停止；没有新的结果。";
  return "进行中；当前内容只是进度，不是最终结果。";
}

export function taskNextStepText(task: MobileTask): string {
  if (task.status === "waiting_approval") return "先处理审批；看不懂就不要批准。";
  if (task.status === "paused") return "确认后再继续；不确定就保持暂停。";
  if (hasVerifiedCompletedResult(task)) return "回电脑端核对结果和来源，再决定是否签收。";
  if (task.status === "completed") return "回电脑端补看结果来源；未核验前不要当作完成。";
  if (task.status === "failed") return "查看电脑端错误后再重试。";
  if (task.status === "cancelled" || task.status === "denied") return "已停止；需要时发起新任务。";
  if (taskActionAllowed(task, "pause")) return "可以先观察；不放心就暂停。";
  return "等待电脑端更新，暂时不用操作。";
}

export function taskStatusBadgeText(task: MobileTask): string {
  if (task.status === "completed" && !hasVerifiedCompletedResult(task)) return "结果待核验";
  return taskStatusLabel(task.status);
}

export function taskStatusBadgeIsDone(task: MobileTask): boolean {
  if (task.status === "completed") return hasVerifiedCompletedResult(task);
  return isMobileTaskTerminal(task);
}

export function taskActionAllowed(task: MobileTask, action: MobileTaskAction): boolean {
  if (Array.isArray(task.available_actions)) return task.available_actions.includes(action);
  if (action === "pause" && typeof task.can_pause === "boolean") return task.can_pause;
  if (action === "resume" && typeof task.can_resume === "boolean") return task.can_resume;
  if (action === "cancel" && typeof task.can_cancel === "boolean") return task.can_cancel;
  if (action === "follow_up" && typeof task.can_follow_up === "boolean") return task.can_follow_up;
  if (action === "resume") return task.status === "paused";
  if (action === "pause") return task.status === "execution";
  if (action === "cancel") return !isMobileTaskTerminal(task);
  return !isMobileTaskTerminal(task);
}

export function isMobileTaskTerminal(task: MobileTask): boolean {
  return task.is_terminal ?? TERMINAL_TASK_STATUSES.includes(task.status);
}

export function isMobileTaskActive(task: MobileTask): boolean {
  return !isMobileTaskTerminal(task) && task.status !== "paused";
}

export function hasVerifiedCompletedResult(task: MobileTask): boolean {
  const evidence = task.completion_evidence;
  return Boolean(
    task.status === "completed" &&
      evidence?.level === "completed_result" &&
      evidence.result_verified === true &&
      evidence.signoff === false,
  );
}

function taskStatusLabel(status: string): string {
  if (status === "created") return "已创建";
  if (status === "planning" || status === "plan_review") return "规划中";
  if (status === "consultation") return "协作中";
  if (status === "waiting_approval") return "待审批";
  if (status === "execution") return "执行中";
  if (status === "final_review") return "复核中";
  if (status === "paused") return "已暂停";
  if (status === "completed") return "已完成";
  if (status === "cancelled" || status === "denied") return "已取消";
  if (status === "failed") return "失败";
  return status || "未知";
}

function statusFallback(status: string): string {
  if (status === "created") return "电脑端已收到任务。";
  if (status === "planning" || status === "plan_review") return "电脑端正在规划，还没有结果。";
  if (status === "consultation") return "电脑端需要补充信息。";
  if (status === "waiting_approval") return "电脑端正在等你处理审批。";
  if (status === "execution") return "电脑端正在执行。";
  if (status === "final_review") return "电脑端正在复核结果。";
  if (status === "paused") return "任务已暂停，电脑端不会继续下一步。";
  if (status === "completed") return "任务已结束，请核对结果。";
  if (status === "cancelled" || status === "denied") return "任务已停止。";
  if (status === "failed") return "任务未完成。";
  return "等待电脑端更新状态。";
}
