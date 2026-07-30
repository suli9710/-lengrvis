import type { TaskEvent, TaskState } from "../../shared/executionTypes";

export interface TimelineUserStatusCopy {
  stageLabel: string;
  stage: string;
  nextStep: string;
  tone: "neutral" | "active" | "success" | "warning" | "danger";
}

export function timelineUserStatusCopy(task: TaskEvent): TimelineUserStatusCopy {
  switch (task.state) {
    case "running":
      return { stageLabel: "当前阶段", stage: "正在执行任务", nextStep: "完成后核对结果与证据", tone: "active" };
    case "blocked":
      return { stageLabel: "当前阶段", stage: "等待你的确认", nextStep: "查看审批内容，再决定是否继续", tone: "warning" };
    case "completed":
      return { stageLabel: "结果", stage: "任务已完成", nextStep: "核对结果，必要时查看证据或回滚预案", tone: "success" };
    case "failed":
      return { stageLabel: "发生了什么", stage: "任务未完成，并已安全停止", nextStep: "重试任务，或打开技术详情查看脱敏原因", tone: "danger" };
    case "denied":
      return { stageLabel: "发生了什么", stage: "任务被安全或权限边界拒绝", nextStep: "查看阻断原因，调整目标或权限后再新建任务", tone: "danger" };
    case "cancelled":
      return { stageLabel: "当前状态", stage: "任务已由用户取消", nextStep: "需要时可调整目标后新建任务", tone: "neutral" };
    case "rolled_back":
      return { stageLabel: "结果", stage: "任务变更已回滚并核验", nextStep: "可查看回滚记录确认各项后态", tone: "success" };
    case "repair_required":
      return { stageLabel: "发生了什么", stage: "回滚未完整完成", nextStep: "查看回滚结果并完成剩余修复", tone: "danger" };
    case "paused":
      return { stageLabel: "当前阶段", stage: "任务已暂停", nextStep: "恢复任务或调整目标", tone: "neutral" };
    default:
      return { stageLabel: "当前阶段", stage: "等待开始", nextStep: "系统会在执行前检查范围和权限", tone: "neutral" };
  }
}

export function workspaceAction(task: TaskEvent): string {
  const labels: Partial<Record<TaskState, string>> = {
    queued: "等待执行",
    running: "正在执行",
    blocked: "等待审批",
    paused: "已暂停",
    completed: "已完成",
    denied: "已拒绝",
    cancelled: "已取消"
  };
  return labels[task.state] ?? "需要复核";
}

export function toneForState(state: TaskState): "neutral" | "success" | "warning" | "danger" | "info" {
  if (state === "completed" || state === "rolled_back") return "success";
  if (state === "blocked") return "warning";
  if (state === "paused" || state === "cancelled") return "neutral";
  if (state === "failed" || state === "denied" || state === "repair_required") return "danger";
  if (state === "running") return "info";
  return "neutral";
}
