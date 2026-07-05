import {
  CheckCircle2,
  LockKeyhole,
  Radio,
  ShieldCheck,
  Sparkles,
  type LucideIcon
} from "lucide-react";

import type { TaskEvent } from "../../../shared/executionTypes";
import {
  isSafeFailureEvidence,
  isVerifiedCompletedResult,
  sortTasksByUpdatedAt,
  taskDisplayState,
  taskDisplayTitle
} from "./officeTaskShared";

export type TaskPilotStepState = "idle" | "current" | "done" | "blocked" | "failed";

export interface TaskPilotStep {
  id: "understand" | "route" | "execute" | "record";
  label: string;
  detail: string;
  state: TaskPilotStepState;
  icon: LucideIcon;
}

export interface TaskPilotSummary {
  title: string;
  detail: string;
  status: string;
  tone: "idle" | "active" | "blocked" | "done" | "failed" | "warning";
  action: "open" | "approve" | "compose";
  actionLabel: string;
  task: TaskEvent | null;
  steps: TaskPilotStep[];
}

export function buildTaskPilotSummary(tasks: TaskEvent[], hasDraft: boolean): TaskPilotSummary {
  const latestTask = sortTasksByUpdatedAt(tasks)[0];

  if (!latestTask) {
    return {
      title: hasDraft ? "准备发起任务" : "等待你的第一个目标",
      detail: hasDraft
        ? "发送后会先理解目标、判断范围和风险，再进入执行。"
        : "输入一句话或使用快捷入口，Lengrvis 会把过程拆成可确认的步骤。",
      status: hasDraft ? "待发送" : "空闲",
      tone: hasDraft ? "active" : "idle",
      action: "compose",
      actionLabel: hasDraft ? "发送后开始" : "输入目标",
      task: null,
      steps: [
        {
          id: "understand",
          label: "理解目标",
          detail: hasDraft ? "已准备分析" : "等待输入",
          state: hasDraft ? "current" : "idle",
          icon: Sparkles
        },
        {
          id: "route",
          label: "确认范围",
          detail: "先看权限和风险",
          state: "idle",
          icon: LockKeyhole
        },
        {
          id: "execute",
          label: "执行任务",
          detail: "过程实时反馈",
          state: "idle",
          icon: Radio
        },
        {
          id: "record",
          label: "结果留痕",
          detail: "完成后可追溯",
          state: "idle",
          icon: CheckCircle2
        }
      ]
    };
  }

  const status = taskDisplayState(latestTask);
  const baseTitle = taskDisplayTitle(latestTask, "最近任务");

  if (latestTask.state === "blocked") {
    return {
      title: baseTitle,
      detail: "任务正在等待你的确认；未批准前不会继续执行高风险操作。",
      status,
      tone: "blocked",
      action: "approve",
      actionLabel: "去确认",
      task: latestTask,
      steps: taskPilotSteps("blocked")
    };
  }

  if (latestTask.state === "failed") {
    return {
      title: baseTitle,
      detail: "任务没有完成。打开记录可以看到失败原因；重新发送前可补充范围或目标。",
      status,
      tone: "failed",
      action: "open",
      actionLabel: "查看原因",
      task: latestTask,
      steps: taskPilotSteps("failed")
    };
  }

  if (isSafeFailureEvidence(latestTask)) {
    return {
      title: baseTitle,
      detail: "任务已安全停止，没有形成完成结果。打开记录可以查看原因，再决定是否重试。",
      status: "安全停止，需处理",
      tone: "failed",
      action: "open",
      actionLabel: "查看原因",
      task: latestTask,
      steps: taskPilotSteps("failed")
    };
  }

  if (latestTask.state === "completed") {
    const verified = isVerifiedCompletedResult(latestTask);
    return {
      title: baseTitle,
      detail: verified
        ? "完成结果已通过核验；可在时间线查看摘要、状态和后续操作。"
        : latestTask.completionEvidence
          ? "任务状态已结束，但还没有可核验的最终结果。建议先核对时间线记录。"
          : "任务状态已结束，但还没有通过结果核验。建议先核对时间线记录。",
      status,
      tone: verified ? "done" : "warning",
      action: "open",
      actionLabel: verified ? "查看结果" : "核对结果",
      task: latestTask,
      steps: taskPilotSteps(verified ? "completed" : "completed_unverified")
    };
  }

  if (latestTask.state === "paused") {
    return {
      title: baseTitle,
      detail: "任务已暂停，进度仍会保留；恢复前不会继续操作。",
      status,
      tone: "blocked",
      action: "open",
      actionLabel: "查看进度",
      task: latestTask,
      steps: taskPilotSteps("paused")
    };
  }

  return {
    title: baseTitle,
    detail: latestTask.state === "queued"
      ? "任务已经进入队列，开始执行前不会重复创建。"
      : "任务正在处理中；结果出现前会继续显示进度和需要你确认的步骤。",
    status,
    tone: "active",
    action: "open",
    actionLabel: "查看进度",
    task: latestTask,
    steps: taskPilotSteps(latestTask.state === "queued" ? "queued" : "running")
  };
}

function taskPilotSteps(stage: TaskEvent["state"] | "idle" | "completed_unverified"): TaskPilotStep[] {
  const states: Record<TaskPilotStep["id"], TaskPilotStepState> = {
    understand: "idle",
    route: "idle",
    execute: "idle",
    record: "idle"
  };

  if (stage === "queued") {
    states.understand = "done";
    states.route = "current";
  } else if (stage === "running") {
    states.understand = "done";
    states.route = "done";
    states.execute = "current";
  } else if (stage === "blocked" || stage === "paused") {
    states.understand = "done";
    states.route = "blocked";
    states.execute = "idle";
  } else if (stage === "completed") {
    states.understand = "done";
    states.route = "done";
    states.execute = "done";
    states.record = "done";
  } else if (stage === "completed_unverified") {
    states.understand = "done";
    states.route = "done";
    states.execute = "done";
    states.record = "blocked";
  } else if (stage === "failed") {
    states.understand = "done";
    states.route = "done";
    states.execute = "failed";
    states.record = "idle";
  }

  return [
    {
      id: "understand",
      label: "理解目标",
      detail: states.understand === "done" ? "已拆解" : "等待输入",
      state: states.understand,
      icon: Sparkles
    },
    {
      id: "route",
      label: "确认范围",
      detail: states.route === "blocked" ? "需要你确认" : states.route === "done" ? "范围已确认" : "检查权限",
      state: states.route,
      icon: states.route === "blocked" ? ShieldCheck : LockKeyhole
    },
    {
      id: "execute",
      label: "执行任务",
      detail: states.execute === "current" ? "正在处理" : states.execute === "failed" ? "未完成" : "实时反馈",
      state: states.execute,
      icon: states.execute === "failed" ? ShieldCheck : Radio
    },
    {
      id: "record",
      label: "结果留痕",
      detail: states.record === "done" ? "可追溯" : states.record === "blocked" ? "记录待核验" : "完成后记录",
      state: states.record,
      icon: CheckCircle2
    }
  ];
}
