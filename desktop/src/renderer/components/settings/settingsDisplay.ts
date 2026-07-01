import type { AppSettings, BackendStatus } from "../../../shared/types";

export function displayMode(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "快速";
  if (mode === "hybrid") return "智能混合";
  return "隐私";
}

export const PERMISSION_MODE_OPTIONS: Array<{
  value: AppSettings["permissionMode"];
  label: string;
  description: string;
}> = [
  { value: "plan", label: "计划", description: "只允许规划和读取。" },
  { value: "default", label: "默认", description: "写操作需要试运行审批。" },
  { value: "trusted_edits", label: "可信编辑", description: "放行可逆可信编辑。" },
  { value: "auto_review", label: "自动审查", description: "规则和安全审查共同放行。" },
  { value: "dont_ask", label: "不打扰", description: "只执行预授权动作。" }
];

export function permissionModeLabel(mode: AppSettings["permissionMode"]): string {
  return PERMISSION_MODE_OPTIONS.find((option) => option.value === mode)?.label ?? "默认";
}

export function modeDescription(mode: AppSettings["mode"]): string {
  if (mode === "efficiency") return "云端优先，适合长推理和网页任务。";
  if (mode === "hybrid") return "云端规划，本机处理敏感内容。";
  return "本机优先，失败时给修复动作。";
}

export function appStatusLabel(state: BackendStatus["state"]): string {
  if (state === "running") return "就绪";
  if (state === "starting") return "启动中";
  if (state === "error") return "需要处理";
  return "不可用";
}
