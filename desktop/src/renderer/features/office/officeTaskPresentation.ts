import type { TaskEvent } from "../../../shared/executionTypes";
import { buildTaskResultTimelineSummary, type TaskResultTimelineSummary } from "../task-results/taskResultTimeline";
import { buildOutcomeCards, type OutcomeCard } from "./officeTaskOutcomes";
import { buildTaskPilotSummary, type TaskPilotSummary } from "./officeTaskPilot";
import {
  isRecentTask,
  isSafeFailureEvidence,
  isVerifiedCompletedResult,
  sortTasksByUpdatedAt,
  taskDisplayState,
  taskDisplayTitle
} from "./officeTaskShared";

export type { OutcomeCard } from "./officeTaskOutcomes";
export type { TaskPilotStep, TaskPilotStepState, TaskPilotSummary } from "./officeTaskPilot";
export { taskDisplayState, taskDisplayTitle };

interface OfficeTaskReadinessItem {
  id: string;
  detail: string;
  state: "ready" | "action" | "warning";
}

interface OfficeTaskTrustItem {
  id: string;
  value: string;
  detail: string;
  state: "ready" | "warning" | "blocked";
}

interface OfficeTaskSkill {
  id: string;
  title: string;
  kind: "prompt" | "view" | "action";
  view?: string;
  trust: { approval: string; rollback: string };
  wizard: { preflight: string; nextStep: string };
}

interface TaskWorkspaceItem {
  label: string;
  value: string;
  detail: string;
  tone: "ready" | "warning" | "blocked";
}

export interface OfficeTaskPresentationInput {
  tasks: TaskEvent[];
  hasDraft: boolean;
  readinessItems: OfficeTaskReadinessItem[];
  trustItems: OfficeTaskTrustItem[];
  pendingApprovalCount: number;
  selectedSkill: OfficeTaskSkill | null;
}

export interface OfficeTaskPresentation {
  currentTasks: TaskEvent[];
  displayedTasks: TaskEvent[];
  activeTaskLabel: string;
  recentTaskLabel: string;
  blockedTaskCount: number;
  runningTaskCount: number;
  taskPilot: TaskPilotSummary;
  resultTimeline: TaskResultTimelineSummary;
  taskWorkspaceItems: TaskWorkspaceItem[];
  outcomeCards: OutcomeCard[];
}

export function deriveOfficeTaskPresentation({
  tasks,
  hasDraft,
  readinessItems,
  trustItems,
  pendingApprovalCount,
  selectedSkill
}: OfficeTaskPresentationInput): OfficeTaskPresentation {
  const currentTasks = getHomeCurrentTasks(tasks);
  const displayedTasks = getHomeVisibleTasks(tasks);

  return {
    currentTasks,
    displayedTasks,
    activeTaskLabel: currentTasks.length > 0 ? summarizeActiveTasks(currentTasks) : "当前没有正在处理的任务",
    recentTaskLabel: displayedTasks.length > 0 ? `显示最近 ${displayedTasks.length} 项` : "还没有最近任务",
    blockedTaskCount: currentTasks.filter((task) => task.state === "blocked").length,
    runningTaskCount: currentTasks.filter((task) => task.state === "running" || task.state === "queued").length,
    taskPilot: buildTaskPilotSummary(tasks, hasDraft),
    resultTimeline: buildTaskResultTimelineSummary(tasks, hasDraft),
    taskWorkspaceItems: buildTaskWorkspaceItems(tasks, readinessItems, trustItems, pendingApprovalCount, selectedSkill),
    outcomeCards: buildOutcomeCards(tasks)
  };
}

function buildTaskWorkspaceItems(
  tasks: TaskEvent[],
  readinessItems: OfficeTaskReadinessItem[],
  trustItems: OfficeTaskTrustItem[],
  pendingApprovalCount: number,
  selectedSkill: OfficeTaskSkill | null
): TaskWorkspaceItem[] {
  const latestTask = sortTasksByUpdatedAt(tasks)[0];
  const scopeItem = readinessItems.find((item) => item.id === "scope");
  const aiItem = trustItems.find((item) => item.id === "ai");
  const uploadItem = trustItems.find((item) => item.id === "upload");
  const hasApproval = pendingApprovalCount > 0 || latestTask?.state === "blocked";
  const hasRollback = Boolean(latestTask?.cleanupPlan) || latestTask?.state === "completed";
  const selectedTool = selectedSkill ? inferWorkspaceToolForSkill(selectedSkill) : "未绑定";
  const selectedApproval = selectedSkill ? selectedSkill.trust.approval : "";
  const selectedRollback = selectedSkill ? selectedSkill.trust.rollback : "";

  return [
    {
      label: "授权范围",
      value: scopeItem?.state === "ready" ? "已限定" : "待选择",
      detail: scopeItem?.detail ?? "文件工具会等待你选择范围",
      tone: scopeItem?.state === "ready" ? "ready" : "warning"
    },
    {
      label: "工具权限",
      value: latestTask ? inferWorkspaceTool(latestTask) : selectedTool,
      detail: latestTask
        ? "按任务类型启用，不会开放全局控制"
        : selectedSkill
          ? `${selectedSkill.title}：${selectedSkill.wizard.preflight}`
          : "选择模板后再绑定工具",
      tone: latestTask || selectedSkill ? "ready" : "warning"
    },
    {
      label: "云端边界",
      value: aiItem?.value ?? "按当前模式",
      detail: uploadItem ? `${aiItem?.detail ?? "按模式执行"}；${uploadItem.detail}` : "按当前模式执行",
      tone: aiItem?.state === "warning" || uploadItem?.state === "warning" ? "warning" : "ready"
    },
    {
      label: "当前动作",
      value: latestTask ? taskDisplayState(latestTask) : selectedSkill ? "待启动" : "空闲",
      detail: latestTask ? taskDisplayTitle(latestTask, "最近任务") : selectedSkill?.wizard.nextStep || "等待第一个目标或任务模板",
      tone: latestTask?.state === "failed" || latestTask?.state === "denied" ? "blocked" : latestTask || selectedSkill ? "ready" : "warning"
    },
    taskResultWorkspaceItem(latestTask, selectedSkill),
    {
      label: "审批点",
      value: hasApproval ? `${pendingApprovalCount || 1} 项待确认` : "暂无待审批",
      detail: hasApproval
        ? "高风险动作会停在这里等待你处理"
        : selectedApproval
          ? `模板策略：${selectedApproval}`
          : "只读或低风险步骤继续执行",
      tone: hasApproval ? "blocked" : "ready"
    },
    {
      label: "回滚/接管",
      value: hasRollback ? "有留痕" : latestTask?.state === "running" ? "执行中" : "待生成",
      detail: hasRollback
        ? "可在时间线查看解释或回滚预案"
        : latestTask?.state === "paused"
          ? "任务已暂停，可从进度入口接回"
          : selectedRollback
            ? `模板策略：${selectedRollback}`
            : "完成或审批后显示更多控制",
      tone: hasRollback || selectedSkill ? "ready" : latestTask ? "warning" : "warning"
    }
  ];
}

function taskResultWorkspaceItem(task: TaskEvent | undefined, selectedSkill: OfficeTaskSkill | null): TaskWorkspaceItem {
  if (!task) {
    return {
      label: "结果状态",
      value: selectedSkill ? "等待启动" : "暂无任务",
      detail: selectedSkill ? "启动后会显示进度、结果核验或需要处理的状态" : "开始任务后这里会标明结果是否已核验",
      tone: "warning"
    };
  }

  if (isVerifiedCompletedResult(task)) {
    return {
      label: "结果状态",
      value: "完成结果已核验",
      detail: "可在时间线查看摘要、记录和后续操作",
      tone: "ready"
    };
  }

  if (isSafeFailureEvidence(task)) {
    return {
      label: "结果状态",
      value: "安全停止，需处理",
      detail: "没有完成结果；先查看原因，再重试或调整范围",
      tone: "blocked"
    };
  }

  if (task.state === "failed" || task.state === "denied" || task.state === "repair_required") {
    return {
      label: "结果状态",
      value: task.state === "denied" ? "已拒绝，需调整" : "未完成，需处理",
      detail: task.state === "denied" ? "安全或权限边界阻止了任务；先查看原因，再调整目标或权限" : "这次没有完成结果；先查看原因，再决定是否重试",
      tone: "blocked"
    };
  }

  if (task.state === "cancelled") {
    return {
      label: "结果状态",
      value: "已取消",
      detail: "任务由用户停止，没有形成新的完成结果",
      tone: "warning"
    };
  }

  if (task.state === "blocked") {
    return {
      label: "结果状态",
      value: "等待你确认",
      detail: "任务已停下，确认前不会继续执行",
      tone: "blocked"
    };
  }

  if (task.state === "running" || task.state === "queued" || task.completionEvidence?.status === "visible_progress") {
    return {
      label: "结果状态",
      value: "有进度，待核验",
      detail: "看得到进展，但还不能当作最终结果",
      tone: "warning"
    };
  }

  if (task.state === "paused") {
    return {
      label: "结果状态",
      value: "已暂停，待接回",
      detail: "进度已保留，恢复前不会继续操作",
      tone: "warning"
    };
  }

  if (task.state === "rolled_back") {
    return {
      label: "结果状态",
      value: "变更已回滚",
      detail: "已执行恢复记录，建议核对文件和应用中的最终状态",
      tone: "ready"
    };
  }

  if (task.completionEvidence?.status === "task_evidence_only") {
    return {
      label: "结果状态",
      value: "仅有任务记录",
      detail: "只说明任务被提交或创建，不能当作完成结果",
      tone: "warning"
    };
  }

  if (task.completionEvidence?.level === "completed_result") {
    return {
      label: "结果状态",
      value: "结果待核验",
      detail: "有结果记录，但还没有通过核验",
      tone: "warning"
    };
  }

  return {
    label: "结果状态",
    value: task.state === "completed" ? "状态已结束，待核验" : "等待处理",
    detail: task.state === "completed" ? "状态结束不等于完成结果，建议先核对记录" : "结果出现前会继续显示进度",
    tone: "warning"
  };
}

function inferWorkspaceTool(task?: TaskEvent): string {
  if (!task) return "未绑定";
  const text = `${task.title} ${task.description} ${task.agent}`.toLowerCase();
  if (task.cleanupPlan || /cleanup|清理|下载|大文件|file/.test(text)) return "文件工具";
  if (/document|文档|总结|问答/.test(text)) return "文档工具";
  if (/computer|system|电脑|系统/.test(text)) return "系统只读";
  if (/browser|网页|浏览器/.test(text)) return "浏览器工具";
  return "任务工具";
}

function inferWorkspaceToolForSkill(skill: OfficeTaskSkill): string {
  if (skill.id === "clean-downloads" || skill.id === "find-large-files") return "文件工具";
  if (skill.id === "summarize-document" || skill.id === "document-qa") return "文档工具";
  if (skill.id === "check-computer") return "系统只读";
  if (skill.kind === "view" && skill.view === "files") return "文件/文档工具";
  return "任务工具";
}

function getHomeCurrentTasks(tasks: TaskEvent[]): TaskEvent[] {
  return sortTasksByUpdatedAt(tasks)
    .filter((task) => task.state === "running" || task.state === "queued" || task.state === "blocked")
    .filter((task) => isRecentTask(task, 24))
    .slice(0, 3);
}

function getHomeVisibleTasks(tasks: TaskEvent[]): TaskEvent[] {
  const activeTasks = getHomeCurrentTasks(tasks);
  const recentFinishedTasks = sortTasksByUpdatedAt(tasks)
    .filter((task) => ["completed", "failed", "paused", "rolled_back", "repair_required"].includes(task.state))
    .filter((task) => isRecentTask(task, 24))
    .slice(0, 3);

  return sortTasksByUpdatedAt([...activeTasks, ...recentFinishedTasks]).slice(0, 3);
}

function summarizeActiveTasks(tasks: TaskEvent[]): string {
  const blockedTask = tasks.find((task) => task.state === "blocked");
  if (blockedTask) return "有项目需要你确认";
  const firstTask = tasks[0];
  if (!firstTask) return "当前没有正在处理的任务";
  return firstTask.title || "正在处理你的请求";
}
