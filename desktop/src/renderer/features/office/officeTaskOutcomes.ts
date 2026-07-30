import type { TaskEvent } from "../../../shared/executionTypes";
import { isSafeFailureEvidence, isVerifiedCompletedResult, sortTasksByUpdatedAt } from "./officeTaskShared";

export interface OutcomeCard {
  id: string;
  title: string;
  eyebrow: string;
  statusLabel: string;
  detail: string;
  action: string;
  tone: "ready" | "warning" | "blocked";
}

export function buildOutcomeCards(tasks: TaskEvent[]): OutcomeCard[] {
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
      emptyAction: "选择模板后点击发送",
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
  if (task.state === "failed" || task.state === "denied" || task.state === "repair_required" || isSafeFailureEvidence(task)) {
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
  if (task.state === "cancelled") {
    return {
      id: "cleanup",
      eyebrow: "清理计划",
      statusLabel: "已取消",
      title: "清理任务已取消",
      detail: "任务已由用户停止；不会继续删除或移动文件。",
      action: "下一步：需要时重新选择范围",
      tone: "warning"
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

  if (task.state === "failed" || task.state === "denied" || isSafeFailureEvidence(task)) {
    return {
      id,
      eyebrow,
      statusLabel: taskOutcomeStatusLabel(task),
      title: task.state === "denied" ? "任务已被拒绝" : "任务未完成",
      detail: task.state === "denied" ? "安全或权限边界阻止了任务形成结果。" : failedDetail,
      action: task.state === "denied" ? "下一步：查看边界并调整目标或权限" : "下一步：查看原因后重试",
      tone: "blocked"
    };
  }

  if (task.state === "cancelled") {
    return {
      id,
      eyebrow,
      statusLabel: "已取消",
      title: "任务已取消",
      detail: "任务由用户停止，没有形成新的完成结果。",
      action: "下一步：需要时调整目标后重新开始",
      tone: "warning"
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

function taskOutcomeStatusLabel(task: TaskEvent): string {
  if (isVerifiedCompletedResult(task)) return "完成结果已核验";
  if (task.state === "blocked") return "等待你确认";
  if (isSafeFailureEvidence(task)) return "安全停止，需处理";
  if (task.state === "failed") return "未完成，需处理";
  if (task.state === "denied") return "已拒绝，需调整";
  if (task.state === "cancelled") return "已取消";
  if (task.state === "repair_required") return "回滚需修复";
  if (task.state === "rolled_back") return "变更已回滚";
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
