import {
  CheckCircle2,
  LockKeyhole,
  Radio,
  ShieldCheck,
  Sparkles,
  type LucideIcon
} from "lucide-react";

import type { TaskEvent } from "../../../shared/types";

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

interface OutcomeCard {
  id: string;
  title: string;
  eyebrow: string;
  statusLabel: string;
  detail: string;
  action: string;
  tone: "ready" | "warning" | "blocked";
}

type TaskPilotStepState = "idle" | "current" | "done" | "blocked" | "failed";

interface TaskPilotStep {
  id: "understand" | "route" | "execute" | "record";
  label: string;
  detail: string;
  state: TaskPilotStepState;
  icon: LucideIcon;
}

interface TaskPilotSummary {
  title: string;
  detail: string;
  status: string;
  tone: "idle" | "active" | "blocked" | "done" | "failed" | "warning";
  action: "open" | "approve" | "compose";
  actionLabel: string;
  task: TaskEvent | null;
  steps: TaskPilotStep[];
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
      tone: latestTask?.state === "failed" ? "blocked" : latestTask || selectedSkill ? "ready" : "warning"
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

  if (task.state === "failed") {
    return {
      label: "结果状态",
      value: "未完成，需处理",
      detail: "这次没有完成结果；先查看原因，再决定是否重试",
      tone: "blocked"
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

function buildOutcomeCards(tasks: TaskEvent[]): OutcomeCard[] {
  const sortedTasks = sortTasksByUpdatedAt(tasks);
  const cleanupTask = sortedTasks.find((task) => task.cleanupPlan);
  const documentTask = sortedTasks.find((task) => /文档|总结|问答|document|summary|qa/i.test(`${task.title} ${task.description} ${task.agent}`));
  const largeFileTask = sortedTasks.find((task) => /大文件|空间|large files?|disk usage/i.test(`${task.title} ${task.description}`));
  const computerTask = sortedTasks.find((task) => /电脑|系统|computer|system/i.test(`${task.title} ${task.description} ${task.agent}`));

  return [
    cleanupOutcomeCard(cleanupTask),
    taskOutcomeCard({
      id: "document",
      task: documentTask,
      eyebrow: "文档问答",
      verifiedTitle: "文档完成结果已核验",
      progressTitle: "文档任务已有记录",
      emptyTitle: "等待文档结果",
      verifiedDetail: "可以继续追问；引用来源随任务记录查看。",
      emptyDetail: "选择“总结本地文档”或“文档问答”后，这里显示摘要和引用入口。",
      progressDetail: "看到文档任务记录，但还不能确认摘要或回答已经完成。",
      runningDetail: "文档任务正在处理，结果出现前先保留输入和范围。",
      blockedDetail: "文档任务停在确认点，批准前不会继续读取或处理更多内容。",
      failedDetail: "这次没有拿到可核验的文档结果，可查看原因后重新选择文档或问题。",
      pausedDetail: "任务已暂停，恢复前不会继续处理文档。",
      verifiedAction: "下一步：继续追问或查看时间线引用",
      progressAction: "下一步：打开时间线核对记录",
      emptyAction: "运行文档模板后可引用结果",
      tone: "ready"
    }),
    taskOutcomeCard({
      id: "large-files",
      task: largeFileTask,
      eyebrow: "查找大文件",
      verifiedTitle: "大文件完成结果已核验",
      progressTitle: "大文件扫描待核验",
      emptyTitle: "等待扫描结果",
      verifiedDetail: "排行和清理建议保留在任务记录里；不会自动删除文件。",
      emptyDetail: "运行“查找大文件”后，这里显示可复核的排行和下一步。",
      progressDetail: "看到扫描记录，但还不能确认排行或清理建议已经形成最终结果。",
      runningDetail: "扫描正在进行，结果出现前不会删除或移动文件。",
      blockedDetail: "任务正在等你确认范围或高风险步骤。",
      failedDetail: "这次没有形成可核验的扫描结果，可重新选择范围后再试。",
      pausedDetail: "扫描已暂停，可从进度入口恢复或重新开始。",
      verifiedAction: "下一步：打开时间线复核候选项",
      progressAction: "下一步：查看记录或重新扫描",
      emptyAction: "先选择范围，再生成只读结果",
      tone: "warning"
    }),
    taskOutcomeCard({
      id: "computer",
      task: computerTask,
      eyebrow: "系统检查",
      verifiedTitle: "系统检查完成结果已核验",
      progressTitle: "系统检查已有记录",
      emptyTitle: "等待只读快照",
      verifiedDetail: "只读状态可作为诊断线索；不会改系统设置。",
      emptyDetail: "运行“检查电脑状态”后，这里显示健康检查和修复入口。",
      progressDetail: "看到系统检查记录，但还不能确认健康结论已经生成。",
      runningDetail: "只读检查正在进行，不会改系统设置。",
      blockedDetail: "检查停在确认点，处理前会继续等待你确认。",
      failedDetail: "这次没有拿到可核验的检查结果，可查看原因后重试只读检查。",
      pausedDetail: "检查已暂停，恢复前不会继续读取状态。",
      verifiedAction: "下一步：查看电脑状态页",
      progressAction: "下一步：查看时间线或重新检查",
      emptyAction: "可一键启动只读检查",
      tone: "ready"
    })
  ];
}

function cleanupOutcomeCard(task?: TaskEvent): OutcomeCard {
  if (!task?.cleanupPlan) {
    return {
      id: "cleanup",
      eyebrow: "清理计划",
      statusLabel: "等待启动",
      title: "等待清理预览",
      detail: "整理下载目录或大文件任务生成结果后，这里显示候选项和审批入口。",
      action: "生成后可复核、审批或查看回滚预案",
      tone: "warning"
    };
  }

  const executableCount = task.cleanupPlan.items.filter((item) => item.disposition === "permanent_delete" || item.disposition === "trash").length;
  const candidateSummary = `${task.cleanupPlan.items.length} 个候选项`;
  if (task.state === "failed" || isSafeFailureEvidence(task)) {
    return {
      id: "cleanup",
      eyebrow: "清理计划",
      statusLabel: taskOutcomeStatusLabel(task),
      title: "清理预览未完成",
      detail: "这次没有形成可核验的清理预览；不会删除或移动文件。",
      action: "下一步：查看原因，重新选择范围后再试",
      tone: "blocked"
    };
  }
  if (task.state === "running" || task.state === "queued") {
    return {
      id: "cleanup",
      eyebrow: "清理计划",
      statusLabel: taskOutcomeStatusLabel(task),
      title: "正在生成清理预览",
      detail: "候选项还在整理中；真正清理前仍会停下让你确认。",
      action: "下一步：等待预览或打开进度",
      tone: "warning"
    };
  }
  if (task.state === "blocked") {
    return {
      id: "cleanup",
      eyebrow: "清理计划",
      statusLabel: taskOutcomeStatusLabel(task),
      title: `${candidateSummary}待确认`,
      detail: `${formatOutcomeBytes(task.cleanupPlan.reclaimableBytes)} 可复核，${executableCount} 项必须审批后才会执行。`,
      action: "下一步：打开审批并逐项确认",
      tone: "blocked"
    };
  }
  if (isVerifiedCompletedResult(task)) {
    return {
      id: "cleanup",
      eyebrow: "清理计划",
      statusLabel: "完成结果已核验",
      title: `${candidateSummary}已核验`,
      detail: `${formatOutcomeBytes(task.cleanupPlan.reclaimableBytes)} 可复核，${executableCount} 项需要审批后才会执行。`,
      action: "下一步：打开时间线复核或审批",
      tone: "ready"
    };
  }
  return {
    id: "cleanup",
    eyebrow: "清理计划",
    statusLabel: taskOutcomeStatusLabel(task),
    title: `${candidateSummary}待核验`,
    detail: `${formatOutcomeBytes(task.cleanupPlan.reclaimableBytes)} 可复核，但还不能当作已核验的最终清理结果。`,
    action: "下一步：打开时间线核对记录",
    tone: "warning"
  };
}

function taskOutcomeCard({
  id,
  task,
  eyebrow,
  verifiedTitle,
  progressTitle,
  emptyTitle,
  verifiedDetail,
  emptyDetail,
  progressDetail,
  runningDetail,
  blockedDetail,
  failedDetail,
  pausedDetail,
  verifiedAction,
  progressAction,
  emptyAction,
  tone
}: {
  id: string;
  task?: TaskEvent;
  eyebrow: string;
  verifiedTitle: string;
  progressTitle: string;
  emptyTitle: string;
  verifiedDetail: string;
  emptyDetail: string;
  progressDetail: string;
  runningDetail: string;
  blockedDetail: string;
  failedDetail: string;
  pausedDetail: string;
  verifiedAction: string;
  progressAction: string;
  emptyAction: string;
  tone: OutcomeCard["tone"];
}): OutcomeCard {
  if (!task) {
    return {
      id,
      eyebrow,
      statusLabel: "等待启动",
      title: emptyTitle,
      detail: emptyDetail,
      action: emptyAction,
      tone: "warning"
    };
  }

  if (task.state === "blocked") {
    return {
      id,
      eyebrow,
      statusLabel: taskOutcomeStatusLabel(task),
      title: "等待你确认",
      detail: blockedDetail,
      action: "下一步：去确认或查看为什么停下",
      tone: "blocked"
    };
  }

  if (task.state === "failed" || isSafeFailureEvidence(task)) {
    return {
      id,
      eyebrow,
      statusLabel: taskOutcomeStatusLabel(task),
      title: "任务未完成",
      detail: failedDetail,
      action: "下一步：查看原因后重试",
      tone: "blocked"
    };
  }

  if (task.state === "paused") {
    return {
      id,
      eyebrow,
      statusLabel: taskOutcomeStatusLabel(task),
      title: "任务已暂停",
      detail: pausedDetail,
      action: "下一步：查看进度或恢复任务",
      tone: "warning"
    };
  }

  if (task.state === "running" || task.state === "queued") {
    return {
      id,
      eyebrow,
      statusLabel: taskOutcomeStatusLabel(task),
      title: task.state === "queued" ? "任务等待执行" : "任务正在处理",
      detail: runningDetail,
      action: "下一步：打开进度查看当前状态",
      tone: "warning"
    };
  }

  if (isVerifiedCompletedResult(task)) {
    return {
      id,
      eyebrow,
      statusLabel: "完成结果已核验",
      title: verifiedTitle,
      detail: verifiedDetail,
      action: verifiedAction,
      tone
    };
  }

  return {
    id,
    eyebrow,
    statusLabel: taskOutcomeStatusLabel(task),
    title: progressTitle,
    detail: unverifiedOutcomeDetail(task, progressDetail),
    action: progressAction,
    tone: "warning"
  };
}

function isVerifiedCompletedResult(task: TaskEvent): boolean {
  const evidence = task.completionEvidence;
  return Boolean(task.state === "completed" && evidence?.level === "completed_result" && evidence.resultVerified === true);
}

function isSafeFailureEvidence(task: TaskEvent): boolean {
  return task.completionEvidence?.level === "safe_failure" || task.completionEvidence?.status === "safe_failure";
}

function taskOutcomeStatusLabel(task: TaskEvent): string {
  if (isVerifiedCompletedResult(task)) return "完成结果已核验";
  if (task.state === "blocked") return "等待你确认";
  if (isSafeFailureEvidence(task)) return "安全停止，需处理";
  if (task.state === "failed") return "未完成，需处理";
  if (task.state === "paused") return "已暂停，可接回";
  if (task.state === "running") return "正在处理";
  if (task.state === "queued") return "等待执行";
  if (task.completionEvidence?.status === "visible_progress") return "有进度，待核验";
  if (task.completionEvidence?.status === "task_evidence_only") return "仅有任务记录";
  if (task.completionEvidence) return "结果待核验";
  if (task.state === "completed") return "状态已结束，未核验";
  return "等待处理";
}

function unverifiedOutcomeDetail(task: TaskEvent, progressDetail: string): string {
  if (!task.completionEvidence) {
    return "任务状态已结束，但还没有通过结果核验。建议先核对时间线记录。";
  }
  if (task.completionEvidence.status === "task_evidence_only") {
    return "这里只看到任务被提交或创建，不能当作完成结果。";
  }
  if (task.completionEvidence.status === "visible_progress") {
    return progressDetail;
  }
  if (task.completionEvidence.level === "completed_result") {
    return "有结果记录，但还没有通过核验。";
  }
  return "任务已有记录，但还不能确认最终结果。";
}

function buildTaskPilotSummary(tasks: TaskEvent[], hasDraft: boolean): TaskPilotSummary {
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

function getHomeCurrentTasks(tasks: TaskEvent[]): TaskEvent[] {
  return sortTasksByUpdatedAt(tasks)
    .filter((task) => task.state === "running" || task.state === "queued" || task.state === "blocked")
    .filter((task) => isRecentTask(task, 24))
    .slice(0, 3);
}

function getHomeVisibleTasks(tasks: TaskEvent[]): TaskEvent[] {
  const activeTasks = getHomeCurrentTasks(tasks);
  const recentFinishedTasks = sortTasksByUpdatedAt(tasks)
    .filter((task) => task.state === "completed" || task.state === "failed" || task.state === "paused")
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

function sortTasksByUpdatedAt(tasks: TaskEvent[]): TaskEvent[] {
  return [...tasks].sort((a, b) => taskUpdatedAt(b) - taskUpdatedAt(a));
}

function isRecentTask(task: TaskEvent, hours: number): boolean {
  const updatedAt = taskUpdatedAt(task);
  if (!updatedAt) return false;
  return Date.now() - updatedAt <= hours * 60 * 60 * 1000;
}

function taskUpdatedAt(task: TaskEvent): number {
  const time = Date.parse(task.updatedAt || task.createdAt);
  return Number.isFinite(time) ? time : 0;
}

function formatOutcomeBytes(bytes?: number): string {
  if (!bytes || !Number.isFinite(bytes)) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

export function taskDisplayState(task: TaskEvent): string {
  if (isSafeFailureEvidence(task)) return "安全停止";
  if (task.state === "completed") return isVerifiedCompletedResult(task) ? "已完成" : "已结束，待核验";
  if (task.state === "running") return "进行中";
  if (task.state === "blocked") return "待审批";
  if (task.state === "paused") return "已暂停";
  if (task.state === "failed") return "未完成";
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
