import type { BackendApproval } from "./api";

export function approvalTitle(approval: BackendApproval): string {
  const labels: Record<string, string> = {
    tool_call: "工具审批",
    file_operation: "文件操作审批",
    cleanup: "清理审批",
    cleanup_plan: "清理计划审批",
    cleanup_execute: "执行清理审批",
    system_change: "系统变更审批",
    browser_action: "浏览器操作审批",
    app_launch: "应用启动审批"
  };
  return labels[approval.approval_type] ?? approval.approval_type.replace(/[_-]/g, " ");
}

export function approvalStatusLabel(status: BackendApproval["status"]): string {
  if (status === "approved") return "已批准";
  if (status === "rejected") return "已拒绝";
  if (status === "expired") return "已过期";
  return "待审批";
}

export function formatPreview(value: unknown): string {
  if (!value || typeof value !== "object") return "暂无预览内容";
  const objectValue = value as Record<string, unknown>;
  const preview = objectValue.diff_preview;
  if (Array.isArray(preview) && preview.length > 0) {
    return preview
      .slice(0, 3)
      .map((item) => {
        if (!item || typeof item !== "object") return String(item);
        const row = item as Record<string, unknown>;
        const action = previewActionLabel(String(row.action ?? row.kind ?? "change"));
        const path = String(row.path ?? row.to ?? row.from ?? "");
        return path ? `${action}: ${path}` : action;
      })
      .join("\n");
  }
  return JSON.stringify(value, null, 2);
}

export function shortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function previewActionLabel(action: string): string {
  const labels: Record<string, string> = {
    change: "变更",
    move: "移动",
    copy: "复制",
    rename: "重命名",
    delete: "删除",
    trash: "移入回收站",
    permanent_delete: "永久删除",
    create_folder: "创建文件夹",
    write_text: "写入文本"
  };
  return labels[action] ?? action;
}
