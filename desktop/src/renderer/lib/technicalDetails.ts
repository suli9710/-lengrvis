import type { TaskEvent } from "../../shared/executionTypes";

export type TechnicalDetailCategory = "execution" | "permissions" | "evidence" | "diagnostics";

export interface TechnicalDetailEntry {
  category: TechnicalDetailCategory;
  label: string;
  value: string;
}

export interface TechnicalDetailGroup {
  category: TechnicalDetailCategory;
  title: string;
  items: TechnicalDetailEntry[];
}

const categoryTitles: Record<TechnicalDetailCategory, string> = {
  execution: "执行链路",
  permissions: "权限与边界",
  evidence: "证据与恢复",
  diagnostics: "诊断信息"
};

const categoryOrder: TechnicalDetailCategory[] = ["execution", "permissions", "evidence", "diagnostics"];

export const EMPTY_TECHNICAL_DETAILS_MESSAGE = "暂无可用的技术详情。";

export function sanitizeTechnicalText(value: unknown): string {
  let text = stringifyTechnicalValue(value);
  if (!text) return "";

  text = text
    .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+\/-]+=*/gi, "$1 [已脱敏]")
    .replace(/\b(sk|pk|rk)-[A-Za-z0-9_-]{12,}\b/gi, "$1-[已脱敏]")
    .replace(
      /\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|password|passwd)(\s*[:=]\s*)["']?[^\s,"'&}]+["']?/gi,
      "$1$2[已脱敏]"
    )
    .replace(/([?&](?:api[_-]?key|access[_-]?token|token|secret|password)=)[^&#\s]+/gi, "$1[已脱敏]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, "[JWT 已脱敏]")
    .replace(/\b[A-Fa-f0-9]{40,}\b/g, "[长密钥已脱敏]")
    .replace(/\b[A-Za-z0-9+/_=]{48,}\b/g, "[长密钥已脱敏]")
    .replace(/\b([A-Za-z]:[\\/]+Users[\\/]+)[^\\/\s"'<>]+/gi, "$1[用户]")
    .replace(/\b(\/Users\/)[^/\s"'<>]+/g, "$1[用户]")
    .replace(/\b(\/home\/)[^/\s"'<>]+/g, "$1[用户]");

  return text;
}

export function groupTechnicalDetails(entries: TechnicalDetailEntry[]): TechnicalDetailGroup[] {
  return categoryOrder
    .map((category) => ({
      category,
      title: categoryTitles[category],
      items: entries.filter((entry) => entry.category === category)
    }))
    .filter((group) => group.items.length > 0);
}

export function technicalDetailsEmptyState(entries: TechnicalDetailEntry[]): string {
  return entries.length ? "" : EMPTY_TECHNICAL_DETAILS_MESSAGE;
}

export function buildTaskTechnicalEntries(task: TaskEvent): TechnicalDetailEntry[] {
  const sourceTaskId = task.sourceTaskId || task.id;
  const entries: TechnicalDetailEntry[] = [
    { category: "execution", label: "Agent", value: task.agent || "未分配" },
    { category: "execution", label: "阶段状态", value: task.state },
    {
      category: "permissions",
      label: "审批状态",
      value: task.state === "blocked" ? "等待审批" : "暂无待审批"
    },
    {
      category: "permissions",
      label: "边界事件",
      value: `${task.boundaryEvents?.length ?? 0} 条`
    },
    {
      category: "evidence",
      label: "步骤录屏",
      value: `${task.recordings?.length ?? 0} 组`
    },
    {
      category: "evidence",
      label: "恢复预案",
      value: task.cleanupPlan || task.state === "completed" ? "可查看" : "尚未生成"
    },
    { category: "diagnostics", label: "任务标识", value: sourceTaskId },
    { category: "diagnostics", label: "运行标识", value: task.runId || "未提供" },
    { category: "diagnostics", label: "更新时间", value: task.updatedAt }
  ];

  return entries.map((entry) => ({ ...entry, value: sanitizeTechnicalText(entry.value) }));
}

function stringifyTechnicalValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
